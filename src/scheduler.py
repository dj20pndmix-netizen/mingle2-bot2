import asyncio
import logging
import random
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Dict, List, Optional

from src.bot_engine import Mingle2BotEngine
from src.config import settings
from src import database

logger = logging.getLogger("mingle2.scheduler")


class BotScheduler:
    """Orchestrates periodic scanning, timed like-back execution, and the Task Automation Hub."""

    def __init__(self, broadcast_callback: Optional[Callable[[Dict[str, Any]], Any]] = None):
        self.state: str = "STOPPED"  # RUNNING, PAUSED, STOPPED
        self.broadcast_callback = broadcast_callback
        self.engine = Mingle2BotEngine(log_callback=self._on_engine_log)
        self._scan_task: Optional[asyncio.Task] = None
        self._exec_task: Optional[asyncio.Task] = None
        self._task_worker: Optional[asyncio.Task] = None
        self._is_scanning: bool = False
        self._is_executing: bool = False
        self._last_scan_time: Optional[datetime] = None

    async def _emit(self, event_type: str, data: Any = None) -> None:
        """Emit real-time WebSocket event to all connected dashboard clients."""
        if self.broadcast_callback:
            payload = {
                "event": event_type,
                "data": data,
                "bot_state": self.state,
                "timestamp": datetime.now(timezone.utc).isoformat(),
            }
            try:
                res = self.broadcast_callback(payload)
                if asyncio.iscoroutine(res):
                    await res
            except Exception as e:
                logger.error(f"Error in broadcast callback: {e}")

    async def _on_engine_log(self, level: str, msg: str) -> None:
        """Handle log line from engine: save to DB and broadcast."""
        log_entry = await database.add_log(level, msg)
        await self._emit("log_entry", log_entry)

    async def start(self) -> None:
        """Start or resume the bot background scheduler and task dispatcher."""
        if self.state == "RUNNING":
            return

        self.state = "RUNNING"
        await self._on_engine_log("SUCCESS", "Mingle2 automation bot started.")
        await self._emit("state_change", {"state": self.state})

        if not self._scan_task or self._scan_task.done():
            self._scan_task = asyncio.create_task(self._scan_loop())
        if not self._exec_task or self._exec_task.done():
            self._exec_task = asyncio.create_task(self._execution_loop())
        if not self._task_worker or self._task_worker.done():
            self._task_worker = asyncio.create_task(self._task_worker_loop())

    async def pause(self) -> None:
        """Pause the bot."""
        self.state = "PAUSED"
        await self._on_engine_log("WARN", "Mingle2 automation bot paused.")
        await self._emit("state_change", {"state": self.state})

    async def stop(self) -> None:
        """Stop the bot and cancel background tasks."""
        self.state = "STOPPED"
        await self._on_engine_log("INFO", "Mingle2 automation bot stopped.")
        await self._emit("state_change", {"state": self.state})

        if self._scan_task and not self._scan_task.done():
            self._scan_task.cancel()
        if self._exec_task and not self._exec_task.done():
            self._exec_task.cancel()
        if self._task_worker and not self._task_worker.done():
            self._task_worker.cancel()

        await self.engine.close_context()

    async def trigger_manual_scan(self) -> Dict[str, Any]:
        """Manually trigger a scan immediately."""
        if self._is_scanning:
            return {"status": "busy", "message": "A scan is already in progress."}

        self.engine.is_login_mode = False
        asyncio.create_task(self._run_scan_cycle(is_manual=True))
        return {"status": "started", "message": "Manual scan triggered."}

    async def trigger_like_now(self, user_id: str) -> Dict[str, Any]:
        """Bypass the 5-minute timer and like a user immediately."""
        user = await database.get_like_by_user_id(user_id)
        if not user:
            # Check cached online or search users
            cached_online = await database.get_cached_search_results("ONLINE")
            found = next((u for u in cached_online if str(u.get("user_id")) == str(user_id)), None)
            if not found:
                cached_search = await database.get_cached_search_results("SEARCH")
                found = next((u for u in cached_search if str(u.get("user_id") or u.get("platform_profile_id")) == str(user_id)), None)

            now = datetime.now(timezone.utc)
            uname = str(found.get("username") or found.get("display_name", f"User {user_id}")) if found else f"User {user_id}"
            user = await database.add_or_get_liker(
                user_id=str(user_id),
                username=uname,
                profile_url=found.get("profile_url") or f"https://mingle2.com/users/view/{user_id}",
                avatar_img_url=found.get("avatar_url") or found.get("photo_reference") or "",
                age_location=found.get("age_location") or "",
                detected_at=now,
                scheduled_like_at=now,
            )

        if user.get("status") in ("COMPLETED", "LIKED"):
            return {"status": "already_liked", "message": f"{user['username']} has already been liked."}

        await self._on_engine_log("INFO", f"Triggering immediate like for {user['username']}...")
        asyncio.create_task(self._execute_single_like(user))
        return {"status": "processing", "message": f"Like-now initiated for {user['username']}."}

    async def cancel_queue_item(self, user_id: str) -> Dict[str, Any]:
        """Cancel a pending like."""
        updated = await database.update_status(user_id, "SKIPPED", error_message="Manually cancelled by user")
        if updated:
            await self._on_engine_log("WARN", f"Cancelled scheduled like for {updated['username']}.")
            await self._emit("queue_updated", updated)
            stats = await database.get_stats()
            await self._emit("stats_updated", stats)
            return {"status": "success", "message": "Item marked as Skipped."}
        return {"status": "error", "message": "Item not found."}

    async def _run_scan_cycle(self, is_manual: bool = False) -> None:
        """Run single detection and ingestion pass with duplicate protection and persistent scheduling."""
        if self._is_scanning:
            return

        # Pre-operation session verification for live platform interactions
        if not settings.test_mode:
            is_authenticated = await self.engine.verify_mingle2_session()
            if not is_authenticated:
                await self._on_engine_log("WARN", "Mingle2 session expired. Automation paused — login required.")
                await self.pause()
                return

        self._is_scanning = True
        try:
            detected_users = await self.engine.scan_interested_users()
            self._last_scan_time = datetime.now(timezone.utc)

            # If real likers are detected, clear out simulated test entries
            if detected_users:
                cleared = await database.clear_simulated_likes()
                if cleared > 0:
                    await self._on_engine_log("INFO", f"Cleaned up {cleared} simulated test record(s).")

            added_count = 0
            for u in detected_users:
                user_id = u["user_id"]
                username = u["username"]
                profile_url = u["profile_url"]
                avatar_url = u.get("avatar_img_url")
                age_loc = u.get("age_location")
                detected_at = u.get("detected_at", datetime.now(timezone.utc))

                # 1. If already marked as liked on Mingle2, record as COMPLETED
                if u.get("is_already_liked"):
                    existing = await database.get_like_by_user_id(user_id)
                    if not existing:
                        await database.add_or_get_liker(
                            user_id=user_id,
                            username=username,
                            profile_url=profile_url,
                            avatar_img_url=avatar_url,
                            age_location=age_loc,
                            detected_at=detected_at,
                            scheduled_like_at=detected_at,
                            initial_status="COMPLETED",
                        )
                        await database.update_status(
                            user_id,
                            "COMPLETED",
                            error_message="Already liked on platform",
                            verification_status="VERIFIED",
                            verification_details="Confirmed from platform scan",
                        )
                    continue

                # 2. If paywalled fan preview without real profile ID, mark as IDENTITY_FAILED
                if u.get("is_fan_preview") or user_id.startswith("fan_preview_"):
                    existing = await database.get_like_by_user_id(user_id)
                    if not existing:
                        record, is_new = await database.add_or_get_liker(
                            user_id=user_id,
                            username=username,
                            profile_url=profile_url,
                            avatar_img_url=avatar_url,
                            age_location=age_loc,
                            detected_at=detected_at,
                            scheduled_like_at=detected_at,
                            initial_status="IDENTITY_FAILED",
                        )
                        if is_new:
                            await database.update_status(
                                user_id,
                                "IDENTITY_FAILED",
                                error_message="Paywalled fan preview does not expose direct platform member ID",
                                verification_status="CANNOT_VERIFY",
                                verification_details="Paywalled preview",
                            )
                    continue

                # 3. Calculate persistent scheduled time: default 5 minutes (300s) + jitter
                delay_secs = getattr(settings, "like_delay_seconds", settings.like_delay_minutes * 60)
                jitter_range = max(5, settings.human_jitter_seconds)
                jitter = random.uniform(-jitter_range, jitter_range)
                scheduled_secs = max(20, delay_secs + jitter)
                scheduled_like_at = detected_at + timedelta(seconds=scheduled_secs)

                record, is_new = await database.add_or_get_liker(
                    user_id=user_id,
                    username=username,
                    profile_url=profile_url,
                    avatar_img_url=avatar_url,
                    age_location=age_loc,
                    detected_at=detected_at,
                    scheduled_like_at=scheduled_like_at,
                    initial_status="WAITING",
                    is_test_mode=settings.test_mode,
                )

                if is_new:
                    added_count += 1
                    minutes = scheduled_secs / 60.0
                    await self._on_engine_log(
                        "INFO",
                        f"Detected '{username}'. Scheduled like-back in {minutes:.1f}m (Jitter: {jitter:+.1f}s)",
                    )
                    await self._emit("new_liker_detected", record)

            if added_count > 0 or is_manual:
                stats = await database.get_stats()
                await self._emit("stats_updated", stats)
                queue_items = await database.get_likes(limit=200)
                await self._emit("queue_refreshed", queue_items)

        except Exception as e:
            await self._on_engine_log("ERROR", f"Scan cycle error: {e}")
        finally:
            self._is_scanning = False

    async def _execute_single_like(self, user: Dict[str, Any]) -> None:
        """Perform verified like-back on a single user and update database."""
        user_id = user["user_id"]
        username = user["username"]

        # Pre-operation session verification for live platform interactions
        if not settings.test_mode:
            is_authenticated = await self.engine.verify_mingle2_session()
            if not is_authenticated:
                await database.update_status(
                    user_id,
                    "SESSION_EXPIRED",
                    error_message="Session expired before action execution. Login required.",
                    verification_status="UNVERIFIED",
                )
                await self._on_engine_log("WARN", f"Session expired while preparing like-back for '{username}'. Pausing automation.")
                await self.pause()
                return

        # 1. Transition state: EXECUTING
        executing_record = await database.update_status(user_id, "EXECUTING")
        if executing_record:
            await self._emit("queue_updated", executing_record)

        # 2. Call engine.like_user with Test Mode support
        try:
            res = await self.engine.like_user(user, is_test_mode=settings.test_mode)
            if isinstance(res, tuple) and len(res) == 3:
                success, message, action_state = res
            elif isinstance(res, tuple) and len(res) == 2:
                success, message = res
                action_state = "COMPLETED" if success else "EXECUTION_FAILED"
            else:
                success, message, action_state = False, "Unknown response from engine", "EXECUTION_FAILED"
        except Exception as err:
            success, message, action_state = False, str(err), "TEMPORARY_ERROR"

        # 3. Transition to final state based on verified platform response
        if success:
            updated = await database.update_status(
                user_id,
                "COMPLETED",
                verification_status="VERIFIED",
                verification_details=message,
            )
            await self._on_engine_log("SUCCESS", f"Liked back '{username}' successfully (Verified: {message}).")
        else:
            verif_status = "MISMATCH" if action_state == "IDENTITY_FAILED" else (
                "CANNOT_VERIFY" if action_state == "VERIFICATION_FAILED" else "UNVERIFIED"
            )
            updated = await database.update_status(
                user_id,
                action_state,
                error_message=message,
                verification_status=verif_status,
                verification_details=message,
            )
            await self._on_engine_log("ERROR", f"Failed liking back '{username}': {message}")

        if updated:
            await self._emit("queue_updated", updated)
        stats = await database.get_stats()
        await self._emit("stats_updated", stats)

    async def _scan_loop(self) -> None:
        """Background periodic loop to scan for new likes."""
        await asyncio.sleep(2)  # initial brief warm-up
        while True:
            try:
                if self.engine.is_login_mode:
                    await asyncio.sleep(5)
                    continue

                if self.state == "RUNNING":
                    await self._run_scan_cycle(is_manual=False)

                interval = max(15, settings.scan_interval_seconds)
                await asyncio.sleep(interval)
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Error in scan loop: {e}")
                await asyncio.sleep(15)

    async def _execution_loop(self) -> None:
        """Background loop polling for pending likes whose independent countdown has elapsed."""
        await asyncio.sleep(5)
        while True:
            try:
                if self.engine.is_login_mode:
                    await asyncio.sleep(5)
                    continue

                if self.state == "RUNNING" and not self._is_executing:
                    self._is_executing = True
                    # 1. Process pending scheduled items whose persistent 5-minute countdown has elapsed
                    now = datetime.now(timezone.utc)
                    pending_items = await database.get_pending_likes(before_time=now, limit=3)

                    if pending_items:
                        for item in pending_items:
                            if self.state != "RUNNING":
                                break
                            await self._execute_single_like(item)
                            await asyncio.sleep(random.uniform(2.5, 5.0))

                    self._is_executing = False

                interval = max(10, min(30, settings.scan_interval_seconds // 2))
                await asyncio.sleep(interval)
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Error in execution loop: {e}")
                self._is_executing = False
                await asyncio.sleep(15)

    # ========================================================================
    # TASK AUTOMATION HUB (DISPATCHER)
    # ========================================================================
    async def _task_worker_loop(self) -> None:
        """Background loop polling for queued automation tasks."""
        await asyncio.sleep(3)
        while True:
            try:
                if self.engine.is_login_mode:
                    await asyncio.sleep(5)
                    continue

                # Query pending task
                pending_tasks = await database.get_tasks(status="PENDING", limit=1)
                if pending_tasks:
                    task = pending_tasks[0]
                    await self._execute_task(task)

                await asyncio.sleep(5)
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Error in task worker loop: {e}")
                await asyncio.sleep(10)

    async def _execute_task(self, task: Dict[str, Any]) -> None:
        """Execute a queued automation task step-by-step with real-time updates."""
        task_id = task["id"]
        task_type = task["task_type"]
        params = task.get("params", {})

        await database.update_task_progress(
            task_id=task_id,
            progress=5,
            current_step="Starting task execution...",
            status="RUNNING",
        )
        await self._emit("task_updated", await database.get_task_by_id(task_id))
        await self._on_engine_log("INFO", f"[TASK #{task_id}] Started task: {task_type}")

        try:
            if task_type == "AUTO_LIKE_BACK":
                await self._task_auto_like_back(task_id)
            elif task_type == "LIKE_ONLINE_USERS":
                limit = int(params.get("limit", 10))
                await self._task_like_online_users(task_id, limit)
            elif task_type == "BULK_SEARCH_AND_LIKE":
                limit = int(params.get("limit", 10))
                await self._task_bulk_search_and_like(task_id, params, limit)
            elif task_type == "AUTO_CHECK_INBOX":
                await self._task_auto_check_inbox(task_id)
            elif task_type == "SYNC_FULL_ACCOUNT":
                await self._task_sync_full_account(task_id)
            else:
                await database.update_task_progress(
                    task_id=task_id,
                    progress=100,
                    current_step=f"Unknown task type: {task_type}",
                    status="FAILED",
                    error_message=f"Unsupported task type: {task_type}",
                )

        except Exception as e:
            logger.error(f"Task #{task_id} failed: {e}")
            await database.update_task_progress(
                task_id=task_id,
                progress=0,
                current_step=f"Failed with error: {e}",
                status="FAILED",
                error_message=str(e),
            )
            await self._on_engine_log("ERROR", f"[TASK #{task_id}] Failed: {e}")

        # Final broadcast
        final_task = await database.get_task_by_id(task_id)
        await self._emit("task_updated", final_task)
        stats = await database.get_stats()
        await self._emit("stats_updated", stats)

    async def _task_auto_like_back(self, task_id: int) -> None:
        """Task: Autonomously like back candidates on MutualMatch and reduce incoming likes count."""
        task = await database.get_task_by_id(task_id)
        params = task.get("params", {}) if task else {}
        limit = int(params.get("limit", 25))

        await database.update_task_progress(task_id, 10, f"Connecting to Mingle2 MutualMatch engine (target: {limit} likes)...")
        await self._emit("task_updated", await database.get_task_by_id(task_id))

        result = await self.engine.auto_like_mutual_matches(limit=limit, task_id=task_id)
        liked_count = result.get("count", 0)
        final_badge = result.get("final_badge", "updated")

        stats = await database.get_stats()
        await self._emit("stats_updated", stats)
        queue_items = await database.get_likes(limit=200)
        await self._emit("queue_refreshed", queue_items)

        await database.update_task_progress(
            task_id=task_id,
            progress=100,
            current_step=f"Completed! Successfully liked back {liked_count} user(s). Incoming likes badge: {final_badge}.",
            status="COMPLETED",
            result_summary=f"Liked back {liked_count} users (Badge: {final_badge})",
        )
        await self._on_engine_log("SUCCESS", f"[TASK #{task_id}] Auto-Like Back task completed! Liked {liked_count} user(s).")

    async def _task_like_online_users(self, task_id: int, limit: int) -> None:
        """Task: Find online users and autonomously like their profiles."""
        await database.update_task_progress(task_id, 20, "Scraping currently active online users...")
        await self._emit("task_updated", await database.get_task_by_id(task_id))

        users = await self.engine.fetch_online_users(limit=limit)
        if not users:
            await database.update_task_progress(
                task_id=task_id,
                progress=100,
                current_step="No active online users found to like.",
                status="COMPLETED",
                result_summary="0 users liked",
            )
            return

        targets = users[:limit]
        total = len(targets)
        liked_count = 0

        for idx, u in enumerate(targets):
            # Check if task was cancelled while running
            current = await database.get_task_by_id(task_id)
            if current and current.get("status") == "CANCELLED":
                await self._on_engine_log("WARN", f"[TASK #{task_id}] Task cancelled by user.")
                return

            pct = 20 + int(((idx + 1) / total) * 75)
            username = u.get("username", "User")
            await database.update_task_progress(task_id, pct, f"Liking online user {idx+1}/{total}: '{username}'...")
            await self._emit("task_updated", await database.get_task_by_id(task_id))

            success, _ = await self.engine.like_user(u)
            if success:
                liked_count += 1
            await asyncio.sleep(random.uniform(2.5, 5.0))

        await database.update_task_progress(
            task_id=task_id,
            progress=100,
            current_step=f"Successfully liked {liked_count}/{total} online users.",
            status="COMPLETED",
            result_summary=f"Liked {liked_count} of {total} users",
        )
        await self._on_engine_log("SUCCESS", f"[TASK #{task_id}] Online liking completed. Liked {liked_count} users.")

    async def _task_bulk_search_and_like(self, task_id: int, params: Dict[str, Any], limit: int) -> None:
        """Task: Search matching profiles and autonomously like them."""
        await database.update_task_progress(task_id, 20, "Executing search with configured filters...")
        await self._emit("task_updated", await database.get_task_by_id(task_id))

        results = await self.engine.run_search(params)
        if not results:
            await database.update_task_progress(
                task_id=task_id,
                progress=100,
                current_step="Search returned 0 profiles.",
                status="COMPLETED",
                result_summary="0 profiles found",
            )
            return

        targets = results[:limit]
        total = len(targets)
        liked_count = 0

        for idx, u in enumerate(targets):
            current = await database.get_task_by_id(task_id)
            if current and current.get("status") == "CANCELLED":
                return

            pct = 20 + int(((idx + 1) / total) * 75)
            username = u.get("username", "User")
            await database.update_task_progress(task_id, pct, f"Liking match {idx+1}/{total}: '{username}'...")
            await self._emit("task_updated", await database.get_task_by_id(task_id))

            success, _ = await self.engine.like_user(u)
            if success:
                liked_count += 1
            await asyncio.sleep(random.uniform(2.5, 5.0))

        await database.update_task_progress(
            task_id=task_id,
            progress=100,
            current_step=f"Liked {liked_count}/{total} search results.",
            status="COMPLETED",
            result_summary=f"Liked {liked_count} of {total} matches",
        )
        await self._on_engine_log("SUCCESS", f"[TASK #{task_id}] Bulk search & like completed.")

    async def _task_auto_check_inbox(self, task_id: int) -> None:
        """Task: Sync mailbox and refresh conversation threads."""
        await database.update_task_progress(task_id, 30, "Checking inbox conversations...")
        await self._emit("task_updated", await database.get_task_by_id(task_id))

        threads = await self.engine.fetch_inbox_threads()
        await database.update_task_progress(task_id, 70, f"Found {len(threads)} threads. Refreshing unread...")
        await self._emit("task_updated", await database.get_task_by_id(task_id))

        # Check messages for first 3 unread threads
        unread = [t for t in threads if t.get("unread")]
        for t in unread[:3]:
            await self.engine.fetch_thread_messages(t["user_id"])
            await asyncio.sleep(1.0)

        await database.update_task_progress(
            task_id=task_id,
            progress=100,
            current_step=f"Inbox sync complete. {len(threads)} conversation(s) loaded.",
            status="COMPLETED",
            result_summary=f"{len(threads)} threads, {len(unread)} unread",
        )
        await self._on_engine_log("SUCCESS", f"[TASK #{task_id}] Inbox auto-check completed.")

    async def _task_sync_full_account(self, task_id: int) -> None:
        """Task: Full sync across Account, Inbox, Matches, and Online."""
        await database.update_task_progress(task_id, 15, "[1/4] Syncing Account Profile...")
        await self._emit("task_updated", await database.get_task_by_id(task_id))
        await self.engine.fetch_account_profile()

        await database.update_task_progress(task_id, 40, "[2/4] Syncing Inbox Conversations...")
        await self._emit("task_updated", await database.get_task_by_id(task_id))
        await self.engine.fetch_inbox_threads()

        await database.update_task_progress(task_id, 65, "[3/4] Scanning Mutual Likers...")
        await self._emit("task_updated", await database.get_task_by_id(task_id))
        await self.engine.scan_interested_users()

        await database.update_task_progress(task_id, 85, "[4/4] Scanning Online Users...")
        await self._emit("task_updated", await database.get_task_by_id(task_id))
        await self.engine.fetch_online_users(limit=20)

        await database.update_task_progress(
            task_id=task_id,
            progress=100,
            current_step="All Mingle2 modules fully synchronized.",
            status="COMPLETED",
            result_summary="100% platform sync complete",
        )
        await self._on_engine_log("SUCCESS", f"[TASK #{task_id}] Full sync completed successfully.")
