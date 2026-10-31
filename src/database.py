import json
import logging
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

import aiosqlite

from src.config import settings
from src.profile_identity import Profile
from src.like_state_machine import ActionState, VerificationStatus, QueueItem

logger = logging.getLogger("mingle2.database")


async def _ensure_column(db: aiosqlite.Connection, table: str, column: str, col_type: str) -> None:
    """Safely adds column to table if not already present."""
    cursor = await db.execute(f"PRAGMA table_info({table})")
    cols = [row[1] for row in await cursor.fetchall()]
    if column not in cols:
        try:
            await db.execute(f"ALTER TABLE {table} ADD COLUMN {column} {col_type}")
        except Exception as e:
            logger.debug(f"Column {column} in {table} notice: {e}")


async def init_db() -> None:
    """Initialize database tables and indexes for full Command Center sync and state machine."""
    settings.ensure_directories()
    async with aiosqlite.connect(settings.db_path) as db:
        # 1. Normalized Profiles Table
        await db.execute("""
            CREATE TABLE IF NOT EXISTS profiles (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                platform_profile_id TEXT UNIQUE NOT NULL,
                profile_url TEXT NOT NULL,
                display_name TEXT NOT NULL,
                age INTEGER,
                gender TEXT,
                location TEXT,
                profession TEXT,
                interests TEXT,
                headline TEXT,
                about_me TEXT,
                photo_reference TEXT,
                source TEXT DEFAULT 'UNKNOWN',
                is_paywalled_preview INTEGER DEFAULT 0,
                verification_status TEXT DEFAULT 'UNVERIFIED',
                discovered_at TEXT NOT NULL,
                last_verified_at TEXT,
                raw_metadata TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );
        """)
        await db.execute("""
            CREATE INDEX IF NOT EXISTS idx_profiles_platform_id
            ON profiles(platform_profile_id);
        """)

        # 2. Likes Queue Table (action_queue)
        await db.execute("""
            CREATE TABLE IF NOT EXISTS likes_queue (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id TEXT UNIQUE NOT NULL,
                username TEXT NOT NULL,
                profile_url TEXT NOT NULL,
                avatar_img_url TEXT,
                age_location TEXT,
                detected_at TEXT NOT NULL,
                scheduled_like_at TEXT NOT NULL,
                liked_at TEXT,
                status TEXT NOT NULL DEFAULT 'DETECTED',
                error_message TEXT,
                action_type TEXT DEFAULT 'LIKE_BACK',
                started_at TEXT,
                completed_at TEXT,
                attempt_count INTEGER DEFAULT 0,
                max_attempts INTEGER DEFAULT 3,
                verification_status TEXT DEFAULT 'UNVERIFIED',
                verification_details TEXT,
                is_test_mode INTEGER DEFAULT 0,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );
        """)

        # Safely migrate existing likes_queue table columns if upgrading from old version
        await _ensure_column(db, "likes_queue", "action_type", "TEXT DEFAULT 'LIKE_BACK'")
        await _ensure_column(db, "likes_queue", "started_at", "TEXT")
        await _ensure_column(db, "likes_queue", "completed_at", "TEXT")
        await _ensure_column(db, "likes_queue", "attempt_count", "INTEGER DEFAULT 0")
        await _ensure_column(db, "likes_queue", "max_attempts", "INTEGER DEFAULT 3")
        await _ensure_column(db, "likes_queue", "verification_status", "TEXT DEFAULT 'UNVERIFIED'")
        await _ensure_column(db, "likes_queue", "verification_details", "TEXT")
        await _ensure_column(db, "likes_queue", "is_test_mode", "INTEGER DEFAULT 0")
        await _ensure_column(db, "likes_queue", "profile_id", "TEXT")
        await _ensure_column(db, "likes_queue", "display_name", "TEXT")
        await _ensure_column(db, "likes_queue", "scheduled_at", "TEXT")
        await _ensure_column(db, "likes_queue", "last_attempt_at", "TEXT")
        await _ensure_column(db, "likes_queue", "match_status", "TEXT DEFAULT 'UNVERIFIED'")

        await db.execute("""
            CREATE INDEX IF NOT EXISTS idx_likes_status_scheduled
            ON likes_queue(status, scheduled_like_at);
        """)
        await db.execute("""
            CREATE INDEX IF NOT EXISTS idx_likes_user_id
            ON likes_queue(user_id);
        """)

        # 3. Account Profile table (synced from /user/my_profile)
        await db.execute("""
            CREATE TABLE IF NOT EXISTS account_profile (
                user_id TEXT PRIMARY KEY,
                username TEXT NOT NULL,
                headline TEXT,
                about_me TEXT,
                age INTEGER,
                location TEXT,
                avatar_url TEXT,
                photos_json TEXT,
                stats_json TEXT,
                updated_at TEXT NOT NULL
            );
        """)

        # 4. Inbox Threads table
        await db.execute("""
            CREATE TABLE IF NOT EXISTS inbox_threads (
                user_id TEXT PRIMARY KEY,
                username TEXT NOT NULL,
                avatar_url TEXT,
                last_message TEXT,
                last_message_time TEXT,
                unread INTEGER DEFAULT 0,
                updated_at TEXT NOT NULL
            );
        """)

        # 5. Inbox Messages (per thread conversation)
        await db.execute("""
            CREATE TABLE IF NOT EXISTS inbox_messages (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                thread_user_id TEXT NOT NULL,
                sender TEXT NOT NULL,
                message_body TEXT NOT NULL,
                sent_at TEXT,
                created_at TEXT NOT NULL
            );
        """)
        await db.execute("""
            CREATE INDEX IF NOT EXISTS idx_inbox_messages_thread
            ON inbox_messages(thread_user_id);
        """)

        # 6. Automation Tasks Queue (Dispatcher)
        await db.execute("""
            CREATE TABLE IF NOT EXISTS tasks_queue (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                task_type TEXT NOT NULL,
                params_json TEXT,
                status TEXT NOT NULL DEFAULT 'PENDING',
                progress INTEGER DEFAULT 0,
                total_steps INTEGER DEFAULT 0,
                current_step TEXT,
                result_summary TEXT,
                error_message TEXT,
                created_at TEXT NOT NULL,
                started_at TEXT,
                finished_at TEXT
            );
        """)
        await db.execute("""
            CREATE INDEX IF NOT EXISTS idx_tasks_status
            ON tasks_queue(status, id);
        """)

        # 7. Search & Online Users Cache
        await db.execute("""
            CREATE TABLE IF NOT EXISTS search_cache (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id TEXT NOT NULL,
                username TEXT NOT NULL,
                age_location TEXT,
                avatar_url TEXT,
                profile_url TEXT,
                is_online INTEGER DEFAULT 0,
                search_type TEXT DEFAULT 'SEARCH',
                created_at TEXT NOT NULL
            );
        """)

        # 8. Structured Activity Logs
        await db.execute("""
            CREATE TABLE IF NOT EXISTS activity_logs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp TEXT NOT NULL,
                level TEXT NOT NULL,
                event_type TEXT NOT NULL,
                profile_id TEXT,
                message TEXT NOT NULL,
                details_json TEXT
            );
        """)
        await db.execute("""
            CREATE INDEX IF NOT EXISTS idx_activity_logs_event
            ON activity_logs(event_type, timestamp);
        """)

        # 9. Legacy bot_logs table
        await db.execute("""
            CREATE TABLE IF NOT EXISTS bot_logs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp TEXT NOT NULL,
                level TEXT NOT NULL,
                message TEXT NOT NULL
            );
        """)

        # 10. Settings KV
        await db.execute("""
            CREATE TABLE IF NOT EXISTS bot_settings (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL
            );
        """)

        # 11. Search History Table
        await db.execute("""
            CREATE TABLE IF NOT EXISTS search_history (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                query_profession TEXT,
                query_location TEXT,
                query_age_min INTEGER,
                query_age_max INTEGER,
                query_keywords TEXT,
                results_count INTEGER DEFAULT 0,
                created_at TEXT NOT NULL
            );
        """)

        # 12. Reset interrupted/stale executing items and running tasks from prior runs
        await db.execute("UPDATE likes_queue SET status = 'WAITING' WHERE status IN ('EXECUTING', 'VERIFYING')")
        await db.execute("UPDATE tasks_queue SET status = 'CANCELLED', error_message = 'Interrupted by server restart' WHERE status = 'RUNNING'")
        await db.commit()


# ============================================================================
# PROFILES ENTITY CRUD
# ============================================================================
async def upsert_profile(profile: Profile) -> Dict[str, Any]:
    """Persist or update normalized Profile entity."""
    now_str = datetime.now(timezone.utc).isoformat()
    interests_str = json.dumps(profile.interests)
    raw_meta_str = json.dumps(profile.raw_metadata)

    async with aiosqlite.connect(settings.db_path) as db:
        db.row_factory = aiosqlite.Row
        await db.execute(
            """
            INSERT INTO profiles (
                platform_profile_id, profile_url, display_name, age, gender, location,
                profession, interests, headline, about_me, photo_reference, source,
                is_paywalled_preview, verification_status, discovered_at, last_verified_at,
                raw_metadata, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(platform_profile_id) DO UPDATE SET
                profile_url = excluded.profile_url,
                display_name = excluded.display_name,
                age = COALESCE(excluded.age, profiles.age),
                gender = COALESCE(excluded.gender, profiles.gender),
                location = COALESCE(excluded.location, profiles.location),
                profession = COALESCE(excluded.profession, profiles.profession),
                interests = excluded.interests,
                headline = COALESCE(excluded.headline, profiles.headline),
                about_me = COALESCE(excluded.about_me, profiles.about_me),
                photo_reference = COALESCE(excluded.photo_reference, profiles.photo_reference),
                verification_status = excluded.verification_status,
                last_verified_at = COALESCE(excluded.last_verified_at, profiles.last_verified_at),
                raw_metadata = excluded.raw_metadata,
                updated_at = excluded.updated_at
            """,
            (
                profile.platform_profile_id,
                profile.profile_url,
                profile.display_name,
                profile.age,
                profile.gender,
                profile.location,
                profile.profession,
                interests_str,
                profile.headline,
                profile.about_me,
                profile.photo_reference,
                profile.source,
                1 if profile.is_paywalled_preview else 0,
                profile.verification_status,
                profile.discovered_at or now_str,
                profile.last_verified_at,
                raw_meta_str,
                now_str,
                now_str,
            ),
        )
        await db.commit()

        cursor = await db.execute("SELECT * FROM profiles WHERE platform_profile_id = ?", (profile.platform_profile_id,))
        row = await cursor.fetchone()
        return dict(row) if row else profile.to_dict()


async def get_profile(platform_profile_id: str) -> Optional[Profile]:
    """Retrieve normalized profile by platform_profile_id."""
    async with aiosqlite.connect(settings.db_path) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute("SELECT * FROM profiles WHERE platform_profile_id = ?", (platform_profile_id,))
        row = await cursor.fetchone()
        if not row:
            return None
        d = dict(row)
        return Profile.from_dict(d)


# ============================================================================
# LIKE-BACK STATE MACHINE & QUEUE CRUD
# ============================================================================
async def add_or_get_liker(
    user_id: str,
    username: str,
    profile_url: str,
    avatar_img_url: Optional[str] = None,
    age_location: Optional[str] = None,
    detected_at: Optional[datetime] = None,
    scheduled_like_at: Optional[datetime] = None,
    is_test_mode: bool = False,
    initial_status: str = "WAITING",
) -> Tuple[Dict[str, Any], bool]:
    """
    Insert newly detected liker into persistent queue with duplicate protection.
    Returns (item_dict, is_new).
    """
    now = datetime.now(timezone.utc)
    if detected_at is None:
        detected_at = now
    if scheduled_like_at is None:
        scheduled_like_at = detected_at + timedelta(minutes=settings.like_delay_minutes)

    now_str = now.isoformat()
    detected_str = detected_at.isoformat()
    scheduled_str = scheduled_like_at.isoformat()

    async with aiosqlite.connect(settings.db_path) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute("SELECT * FROM likes_queue WHERE user_id = ?", (user_id,))
        row = await cursor.fetchone()
        if row:
            d = dict(row)
            # Recompute real-time remaining countdown seconds
            try:
                s_dt = datetime.fromisoformat(d["scheduled_like_at"])
                d["remaining_seconds"] = max(0.0, (s_dt - datetime.now(timezone.utc)).total_seconds())
            except Exception:
                d["remaining_seconds"] = 0.0
            return d, False

        await db.execute(
            """
            INSERT INTO likes_queue (
                user_id, username, profile_url, avatar_img_url, age_location,
                detected_at, scheduled_like_at, status, action_type, attempt_count,
                max_attempts, verification_status, is_test_mode, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'LIKE_BACK', 0, 3, 'UNVERIFIED', ?, ?, ?)
            """,
            (
                user_id,
                username,
                profile_url,
                avatar_img_url,
                age_location,
                detected_str,
                scheduled_str,
                initial_status,
                1 if is_test_mode else 0,
                now_str,
                now_str,
            ),
        )
        await db.commit()

        cursor = await db.execute("SELECT * FROM likes_queue WHERE user_id = ?", (user_id,))
        new_row = await cursor.fetchone()
        res = dict(new_row)
        try:
            s_dt = datetime.fromisoformat(res["scheduled_like_at"])
            res["remaining_seconds"] = max(0.0, (s_dt - datetime.now(timezone.utc)).total_seconds())
        except Exception:
            res["remaining_seconds"] = 0.0
        return res, True


async def get_pending_likes(before_time: Optional[datetime] = None, limit: int = 50) -> List[Dict[str, Any]]:
    """
    Retrieve items whose 5-minute countdown has elapsed and are ready to execute.
    Supports both WAITING, READY, and legacy PENDING status.
    """
    time_cutoff = (before_time or datetime.now(timezone.utc)).isoformat()
    async with aiosqlite.connect(settings.db_path) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute(
            """
            SELECT * FROM likes_queue
            WHERE status IN ('WAITING', 'READY', 'PENDING') AND scheduled_like_at <= ?
            ORDER BY scheduled_like_at ASC
            LIMIT ?
            """,
            (time_cutoff, limit),
        )
        rows = await cursor.fetchall()
        result = []
        now = datetime.now(timezone.utc)
        for r in rows:
            d = dict(r)
            try:
                s_dt = datetime.fromisoformat(d["scheduled_like_at"])
                d["remaining_seconds"] = max(0.0, (s_dt - now).total_seconds())
            except Exception:
                d["remaining_seconds"] = 0.0
            result.append(d)
        return result


async def update_status(
    user_id: str,
    status: str,
    error_message: Optional[str] = None,
    liked_at: Optional[datetime] = None,
    verification_status: Optional[str] = None,
    verification_details: Optional[str] = None,
) -> Optional[Dict[str, Any]]:
    """Atomically update queue item state machine status and verification details."""
    now_str = datetime.now(timezone.utc).isoformat()
    liked_str = liked_at.isoformat() if liked_at else (now_str if status in ("LIKED", "COMPLETED") else None)

    async with aiosqlite.connect(settings.db_path) as db:
        db.row_factory = aiosqlite.Row

        updates = ["status = ?", "error_message = ?", "updated_at = ?"]
        params: List[Any] = [status, error_message, now_str]

        if liked_str:
            updates.append("liked_at = COALESCE(?, liked_at)")
            params.append(liked_str)

        if status in ("COMPLETED", "LIKED"):
            updates.append("completed_at = COALESCE(completed_at, ?)")
            params.append(now_str)
        elif status == "EXECUTING":
            updates.append("started_at = COALESCE(started_at, ?)")
            params.append(now_str)
            updates.append("attempt_count = attempt_count + 1")

        if verification_status:
            updates.append("verification_status = ?")
            params.append(verification_status)

        if verification_details:
            updates.append("verification_details = ?")
            params.append(verification_details)

        params.append(user_id)
        query = f"UPDATE likes_queue SET {', '.join(updates)} WHERE user_id = ?"
        await db.execute(query, params)
        await db.commit()

        cursor = await db.execute("SELECT * FROM likes_queue WHERE user_id = ?", (user_id,))
        row = await cursor.fetchone()
        if not row:
            return None
        res = dict(row)
        try:
            s_dt = datetime.fromisoformat(res["scheduled_like_at"])
            res["remaining_seconds"] = max(0.0, (s_dt - datetime.now(timezone.utc)).total_seconds())
        except Exception:
            res["remaining_seconds"] = 0.0
        return res


async def get_likes(
    status_filter: Optional[str] = None,
    search: Optional[str] = None,
    is_test_mode: Optional[bool] = None,
    limit: int = 200,
    offset: int = 0,
) -> List[Dict[str, Any]]:
    """Retrieve queue items with real-time remaining countdown and filter support."""
    query = "SELECT * FROM likes_queue WHERE 1=1"
    params: List[Any] = []

    if status_filter and status_filter.upper() != "ALL":
        filter_val = status_filter.upper()
        if filter_val == "PENDING":
            query += " AND status IN ('PENDING', 'WAITING', 'READY', 'QUEUED')"
        elif filter_val == "COMPLETED":
            query += " AND status IN ('COMPLETED', 'LIKED')"
        else:
            query += " AND status = ?"
            params.append(filter_val)

    if is_test_mode is not None:
        query += " AND is_test_mode = ?"
        params.append(1 if is_test_mode else 0)

    if search:
        query += " AND (username LIKE ? OR age_location LIKE ? OR user_id LIKE ?)"
        term = f"%{search}%"
        params.extend([term, term, term])

    query += " ORDER BY id DESC LIMIT ? OFFSET ?"
    params.extend([limit, offset])

    async with aiosqlite.connect(settings.db_path) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute(query, params)
        rows = await cursor.fetchall()

        now = datetime.now(timezone.utc)
        result = []
        for r in rows:
            d = dict(r)
            try:
                s_dt = datetime.fromisoformat(d["scheduled_like_at"])
                d["remaining_seconds"] = max(0.0, (s_dt - now).total_seconds())
            except Exception:
                d["remaining_seconds"] = 0.0
            result.append(d)
        return result


async def get_like_by_user_id(user_id: str) -> Optional[Dict[str, Any]]:
    """Fetch specific user record."""
    async with aiosqlite.connect(settings.db_path) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute("SELECT * FROM likes_queue WHERE user_id = ?", (user_id,))
        row = await cursor.fetchone()
        if not row:
            return None
        d = dict(row)
        try:
            s_dt = datetime.fromisoformat(d["scheduled_like_at"])
            d["remaining_seconds"] = max(0.0, (s_dt - datetime.now(timezone.utc)).total_seconds())
        except Exception:
            d["remaining_seconds"] = 0.0
        return d


async def delete_like(user_id: str) -> bool:
    """Delete a user from the queue."""
    async with aiosqlite.connect(settings.db_path) as db:
        cursor = await db.execute("DELETE FROM likes_queue WHERE user_id = ?", (user_id,))
        await db.commit()
        return cursor.rowcount > 0


async def clear_simulated_likes() -> int:
    """Delete all simulated / test items from the likes queue."""
    async with aiosqlite.connect(settings.db_path) as db:
        cursor = await db.execute("DELETE FROM likes_queue WHERE user_id LIKE 'sim_%' OR is_test_mode = 1")
        await db.commit()
        return cursor.rowcount


async def get_stats() -> Dict[str, Any]:
    """Calculate aggregated queue statistics, state machine metrics, and inbox/tasks counts."""
    async with aiosqlite.connect(settings.db_path) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute("""
            SELECT
                COUNT(*) as total,
                SUM(CASE WHEN status IN ('PENDING', 'WAITING', 'READY', 'QUEUED') THEN 1 ELSE 0 END) as pending,
                SUM(CASE WHEN status IN ('WAITING', 'QUEUED') THEN 1 ELSE 0 END) as waiting,
                SUM(CASE WHEN status = 'READY' THEN 1 ELSE 0 END) as ready,
                SUM(CASE WHEN status = 'EXECUTING' THEN 1 ELSE 0 END) as executing,
                SUM(CASE WHEN status = 'VERIFYING' THEN 1 ELSE 0 END) as verifying,
                SUM(CASE WHEN status IN ('COMPLETED', 'LIKED') THEN 1 ELSE 0 END) as liked,
                SUM(CASE WHEN status IN ('FAILED', 'EXECUTION_FAILED') THEN 1 ELSE 0 END) as failed,
                SUM(CASE WHEN status IN ('VERIFICATION_FAILED', 'IDENTITY_FAILED') THEN 1 ELSE 0 END) as verification_failed,
                SUM(CASE WHEN status = 'SKIPPED' THEN 1 ELSE 0 END) as skipped
            FROM likes_queue
        """)
        row = await cursor.fetchone()
        total = row["total"] or 0
        pending = row["pending"] or 0
        waiting = row["waiting"] or 0
        ready = row["ready"] or 0
        executing = row["executing"] or 0
        verifying = row["verifying"] or 0
        liked = row["liked"] or 0
        failed = row["failed"] or 0
        verification_failed = row["verification_failed"] or 0
        skipped = row["skipped"] or 0

        next_cursor = await db.execute("""
            SELECT scheduled_like_at FROM likes_queue
            WHERE status IN ('PENDING', 'WAITING', 'READY', 'QUEUED')
            ORDER BY scheduled_like_at ASC LIMIT 1
        """)
        next_row = await next_cursor.fetchone()
        next_scheduled = next_row["scheduled_like_at"] if next_row else None

        # Inbox stats
        inbox_cursor = await db.execute("""
            SELECT COUNT(*) as total_threads, SUM(unread) as unread_count FROM inbox_threads
        """)
        inbox_row = await inbox_cursor.fetchone()
        total_threads = inbox_row["total_threads"] or 0
        unread_count = inbox_row["unread_count"] or 0

        # Tasks stats
        tasks_cursor = await db.execute("""
            SELECT
                COUNT(*) as total_tasks,
                SUM(CASE WHEN status = 'RUNNING' THEN 1 ELSE 0 END) as running_tasks,
                SUM(CASE WHEN status = 'PENDING' THEN 1 ELSE 0 END) as pending_tasks
            FROM tasks_queue
        """)
        tasks_row = await tasks_cursor.fetchone()

        return {
            "total_detected": total,
            "pending_count": pending,
            "waiting_count": waiting,
            "ready_count": ready,
            "executing_count": executing,
            "verifying_count": verifying,
            "liked_count": liked,
            "failed_count": failed,
            "verification_failed_count": verification_failed,
            "skipped_count": skipped,
            "next_scheduled_at": next_scheduled,
            "inbox_threads": total_threads,
            "inbox_unread": unread_count,
            "running_tasks": tasks_row["running_tasks"] or 0,
            "pending_tasks": tasks_row["pending_tasks"] or 0,
        }


# ============================================================================
# STRUCTURED ACTIVITY LOGGING
# ============================================================================
async def add_activity_log(
    level: str,
    event_type: str,
    profile_id: Optional[str],
    message: str,
    details: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Append structured audit event log into database."""
    now_str = datetime.now(timezone.utc).isoformat()
    details_str = json.dumps(details) if details else None

    async with aiosqlite.connect(settings.db_path) as db:
        await db.execute(
            """
            INSERT INTO activity_logs (timestamp, level, event_type, profile_id, message, details_json)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (now_str, level.upper(), event_type.upper(), profile_id, message, details_str),
        )
        # Also mirror to bot_logs for backward compatibility
        await db.execute(
            "INSERT INTO bot_logs (timestamp, level, message) VALUES (?, ?, ?)",
            (now_str, level.upper(), f"[{event_type}] {message}"),
        )
        await db.commit()

    return {
        "timestamp": now_str,
        "level": level.upper(),
        "event_type": event_type.upper(),
        "profile_id": profile_id,
        "message": message,
        "details": details or {},
    }


async def get_activity_logs(
    limit: int = 150,
    event_type: Optional[str] = None,
    profile_id: Optional[str] = None,
) -> List[Dict[str, Any]]:
    """Retrieve structured audit logs."""
    query = "SELECT * FROM activity_logs WHERE 1=1"
    params: List[Any] = []

    if event_type:
        query += " AND event_type = ?"
        params.append(event_type.upper())
    if profile_id:
        query += " AND profile_id = ?"
        params.append(profile_id)

    query += " ORDER BY id DESC LIMIT ?"
    params.append(limit)

    async with aiosqlite.connect(settings.db_path) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute(query, params)
        rows = await cursor.fetchall()
        result = []
        for r in reversed(rows):
            d = dict(r)
            try:
                d["details"] = json.loads(d.get("details_json") or "{}")
            except Exception:
                d["details"] = {}
            result.append(d)
        return result


async def add_log(level: str, message: str) -> Dict[str, Any]:
    """Legacy helper: appends event log into database."""
    # Attempt to parse event type if formatted as [EVENT] Message
    event_type = "GENERAL"
    if message.startswith("[") and "]" in message:
        event_type = message[1:message.index("]")].strip()
        msg_body = message[message.index("]") + 1:].strip()
    else:
        msg_body = message

    return await add_activity_log(level, event_type, None, msg_body)


async def get_recent_logs(limit: int = 150) -> List[Dict[str, Any]]:
    """Retrieve recent event logs."""
    return await get_activity_logs(limit=limit)


async def clear_logs() -> None:
    """Clear all stored activity log lines."""
    async with aiosqlite.connect(settings.db_path) as db:
        await db.execute("DELETE FROM activity_logs")
        await db.execute("DELETE FROM bot_logs")
        await db.commit()


# ============================================================================
# ACCOUNT PROFILE
# ============================================================================
async def save_account_profile(profile_data: Dict[str, Any]) -> Dict[str, Any]:
    """Upsert account profile information."""
    now_str = datetime.now(timezone.utc).isoformat()
    user_id = str(profile_data.get("user_id", "my_account"))
    username = str(profile_data.get("username", "Mingle2 User"))
    headline = profile_data.get("headline", "")
    about_me = profile_data.get("about_me", "")
    age = profile_data.get("age")
    location = profile_data.get("location", "")
    avatar_url = profile_data.get("avatar_url", "")
    photos_json = json.dumps(profile_data.get("photos", []))
    stats_json = json.dumps(profile_data.get("stats", {}))

    async with aiosqlite.connect(settings.db_path) as db:
        await db.execute(
            """
            INSERT INTO account_profile (
                user_id, username, headline, about_me, age, location,
                avatar_url, photos_json, stats_json, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(user_id) DO UPDATE SET
                username = excluded.username,
                headline = excluded.headline,
                about_me = excluded.about_me,
                age = excluded.age,
                location = excluded.location,
                avatar_url = excluded.avatar_url,
                photos_json = excluded.photos_json,
                stats_json = excluded.stats_json,
                updated_at = excluded.updated_at
            """,
            (user_id, username, headline, about_me, age, location, avatar_url, photos_json, stats_json, now_str),
        )
        await db.commit()

    return await get_account_profile() or profile_data


async def get_account_profile() -> Optional[Dict[str, Any]]:
    """Retrieve saved account profile."""
    async with aiosqlite.connect(settings.db_path) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute("SELECT * FROM account_profile ORDER BY updated_at DESC LIMIT 1")
        row = await cursor.fetchone()
        if not row:
            return None
        res = dict(row)
        try:
            res["photos"] = json.loads(res.get("photos_json") or "[]")
        except Exception:
            res["photos"] = []
        try:
            res["stats"] = json.loads(res.get("stats_json") or "{}")
        except Exception:
            res["stats"] = {}
        return res


# ============================================================================
# INBOX / MESSAGES
# ============================================================================
async def save_inbox_threads(threads: List[Dict[str, Any]]) -> None:
    """Save or update list of conversations."""
    now_str = datetime.now(timezone.utc).isoformat()
    async with aiosqlite.connect(settings.db_path) as db:
        for t in threads:
            user_id = str(t["user_id"])
            username = str(t.get("username", "User"))
            avatar_url = t.get("avatar_url", "")
            last_message = t.get("last_message", "")
            last_message_time = t.get("last_message_time", "")
            unread = 1 if t.get("unread") else 0

            await db.execute(
                """
                INSERT INTO inbox_threads (
                    user_id, username, avatar_url, last_message, last_message_time, unread, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(user_id) DO UPDATE SET
                    username = excluded.username,
                    avatar_url = COALESCE(NULLIF(excluded.avatar_url, ''), inbox_threads.avatar_url),
                    last_message = excluded.last_message,
                    last_message_time = excluded.last_message_time,
                    unread = excluded.unread,
                    updated_at = excluded.updated_at
                """,
                (user_id, username, avatar_url, last_message, last_message_time, unread, now_str),
            )
        await db.commit()


async def get_inbox_threads() -> List[Dict[str, Any]]:
    """Retrieve all conversations ordered by recent activity."""
    async with aiosqlite.connect(settings.db_path) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute("SELECT * FROM inbox_threads ORDER BY updated_at DESC")
        rows = await cursor.fetchall()
        return [dict(r) for r in rows]


async def save_thread_messages(thread_user_id: str, messages: List[Dict[str, Any]]) -> None:
    """Save parsed conversation history for a thread."""
    now_str = datetime.now(timezone.utc).isoformat()
    async with aiosqlite.connect(settings.db_path) as db:
        for m in messages:
            sender = m.get("sender", "Them")
            body = m.get("message_body", "")
            sent_at = m.get("sent_at", "")
            cursor = await db.execute(
                "SELECT id FROM inbox_messages WHERE thread_user_id = ? AND sender = ? AND message_body = ?",
                (thread_user_id, sender, body),
            )
            if not await cursor.fetchone():
                await db.execute(
                    """
                    INSERT INTO inbox_messages (thread_user_id, sender, message_body, sent_at, created_at)
                    VALUES (?, ?, ?, ?, ?)
                    """,
                    (thread_user_id, sender, body, sent_at, now_str),
                )
        await db.commit()


async def get_thread_messages(thread_user_id: str) -> List[Dict[str, Any]]:
    """Retrieve chat history for a thread."""
    async with aiosqlite.connect(settings.db_path) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute(
            "SELECT * FROM inbox_messages WHERE thread_user_id = ? ORDER BY id ASC",
            (thread_user_id,),
        )
        rows = await cursor.fetchall()
        return [dict(r) for r in rows]


async def add_inbox_message(thread_user_id: str, sender: str, message_body: str, sent_at: Optional[str] = None) -> Dict[str, Any]:
    """Append a single chat message (e.g. after sending)."""
    now_str = datetime.now(timezone.utc).isoformat()
    async with aiosqlite.connect(settings.db_path) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute(
            """
            INSERT INTO inbox_messages (thread_user_id, sender, message_body, sent_at, created_at)
            VALUES (?, ?, ?, ?, ?)
            """,
            (thread_user_id, sender, message_body, sent_at or now_str, now_str),
        )
        await db.execute(
            """
            UPDATE inbox_threads
            SET last_message = ?, last_message_time = ?, updated_at = ?
            WHERE user_id = ?
            """,
            (message_body, now_str, now_str, thread_user_id),
        )
        await db.commit()

        new_id = cursor.lastrowid
        c2 = await db.execute("SELECT * FROM inbox_messages WHERE id = ?", (new_id,))
        row = await c2.fetchone()
        return dict(row)


# ============================================================================
# TASK AUTOMATION HUB (DISPATCHER)
# ============================================================================
async def create_task(task_type: str, params: Optional[Dict[str, Any]] = None, total_steps: int = 1) -> Dict[str, Any]:
    """Create a new automation task in the queue."""
    now_str = datetime.now(timezone.utc).isoformat()
    params_str = json.dumps(params or {})

    async with aiosqlite.connect(settings.db_path) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute(
            """
            INSERT INTO tasks_queue (
                task_type, params_json, status, progress, total_steps,
                current_step, created_at
            ) VALUES (?, ?, 'PENDING', 0, ?, 'Queued in task manager', ?)
            """,
            (task_type, params_str, total_steps, now_str),
        )
        await db.commit()
        task_id = cursor.lastrowid
        c2 = await db.execute("SELECT * FROM tasks_queue WHERE id = ?", (task_id,))
        row = await c2.fetchone()
        res = dict(row)
        res["params"] = json.loads(res.get("params_json") or "{}")
        return res


async def get_tasks(status: Optional[str] = None, limit: int = 50) -> List[Dict[str, Any]]:
    """Retrieve automation tasks list."""
    query = "SELECT * FROM tasks_queue WHERE 1=1"
    params: List[Any] = []
    if status and status.upper() != "ALL":
        query += " AND status = ?"
        params.append(status.upper())
    query += " ORDER BY id DESC LIMIT ?"
    params.append(limit)

    async with aiosqlite.connect(settings.db_path) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute(query, params)
        rows = await cursor.fetchall()
        result = []
        for r in rows:
            d = dict(r)
            try:
                d["params"] = json.loads(d.get("params_json") or "{}")
            except Exception:
                d["params"] = {}
            result.append(d)
        return result


async def get_task_by_id(task_id: int) -> Optional[Dict[str, Any]]:
    """Fetch specific task details."""
    async with aiosqlite.connect(settings.db_path) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute("SELECT * FROM tasks_queue WHERE id = ?", (task_id,))
        row = await cursor.fetchone()
        if not row:
            return None
        d = dict(row)
        try:
            d["params"] = json.loads(d.get("params_json") or "{}")
        except Exception:
            d["params"] = {}
        return d


async def update_task_progress(
    task_id: int,
    progress: int,
    current_step: str,
    status: Optional[str] = None,
    result_summary: Optional[str] = None,
    error_message: Optional[str] = None,
) -> Optional[Dict[str, Any]]:
    """Update task progress and execution state."""
    now_str = datetime.now(timezone.utc).isoformat()
    async with aiosqlite.connect(settings.db_path) as db:
        db.row_factory = aiosqlite.Row

        updates = ["progress = ?", "current_step = ?"]
        vals: List[Any] = [progress, current_step]

        if status:
            updates.append("status = ?")
            vals.append(status)
            if status == "RUNNING":
                updates.append("started_at = COALESCE(started_at, ?)")
                vals.append(now_str)
            elif status in ("COMPLETED", "FAILED", "CANCELLED"):
                updates.append("finished_at = ?")
                vals.append(now_str)

        if result_summary is not None:
            updates.append("result_summary = ?")
            vals.append(result_summary)

        if error_message is not None:
            updates.append("error_message = ?")
            vals.append(error_message)

        vals.append(task_id)
        query = f"UPDATE tasks_queue SET {', '.join(updates)} WHERE id = ?"
        await db.execute(query, vals)
        await db.commit()

        cursor = await db.execute("SELECT * FROM tasks_queue WHERE id = ?", (task_id,))
        row = await cursor.fetchone()
        if not row:
            return None
        d = dict(row)
        try:
            d["params"] = json.loads(d.get("params_json") or "{}")
        except Exception:
            d["params"] = {}
        return d


async def cancel_task(task_id: int) -> bool:
    """Mark a pending or running task as CANCELLED."""
    now_str = datetime.now(timezone.utc).isoformat()
    async with aiosqlite.connect(settings.db_path) as db:
        cursor = await db.execute(
            """
            UPDATE tasks_queue
            SET status = 'CANCELLED', current_step = 'Cancelled by user', finished_at = ?
            WHERE id = ? AND status IN ('PENDING', 'RUNNING')
            """,
            (now_str, task_id),
        )
        await db.commit()
        return cursor.rowcount > 0


async def clear_completed_tasks() -> None:
    """Clear finished, failed, or cancelled tasks."""
    async with aiosqlite.connect(settings.db_path) as db:
        await db.execute("DELETE FROM tasks_queue WHERE status IN ('COMPLETED', 'FAILED', 'CANCELLED')")
        await db.commit()


async def clear_all_tasks() -> int:
    """Delete all tasks from the queue."""
    async with aiosqlite.connect(settings.db_path) as db:
        cursor = await db.execute("DELETE FROM tasks_queue")
        await db.commit()
        return cursor.rowcount


# ============================================================================
# SEARCH & ONLINE CACHE
# ============================================================================
async def save_search_results(results: List[Dict[str, Any]], search_type: str = "SEARCH") -> None:
    """Cache search or online users results."""
    now_str = datetime.now(timezone.utc).isoformat()
    async with aiosqlite.connect(settings.db_path) as db:
        await db.execute("DELETE FROM search_cache WHERE search_type = ?", (search_type,))
        for u in results:
            raw_uid = str(u.get("platform_profile_id") or u.get("user_id") or "")
            await db.execute(
                """
                INSERT INTO search_cache (
                    user_id, username, age_location, avatar_url, profile_url, is_online, search_type, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    raw_uid,
                    str(u.get("username") or u.get("display_name", "User")),
                    u.get("location") or u.get("age_location", ""),
                    u.get("photo_reference") or u.get("avatar_url") or u.get("avatar_img_url", ""),
                    u.get("profile_url", ""),
                    1 if u.get("is_online") else 0,
                    search_type,
                    now_str,
                ),
            )
        await db.commit()


async def get_cached_search_results(search_type: str = "SEARCH", limit: int = 100) -> List[Dict[str, Any]]:
    """Retrieve cached search or online users."""
    async with aiosqlite.connect(settings.db_path) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute(
            "SELECT * FROM search_cache WHERE search_type = ? ORDER BY id ASC LIMIT ?",
            (search_type, limit),
        )
        rows = await cursor.fetchall()
        return [dict(r) for r in rows]


# ============================================================================
# SETTINGS KV
# ============================================================================
async def get_stored_settings() -> Dict[str, Any]:
    """Fetch stored settings overrides."""
    async with aiosqlite.connect(settings.db_path) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute("SELECT key, value FROM bot_settings")
        rows = await cursor.fetchall()
        result = {}
        for r in rows:
            try:
                result[r["key"]] = json.loads(r["value"])
            except Exception:
                result[r["key"]] = r["value"]
        return result


async def save_stored_setting(key: str, value: Any) -> None:
    """Save setting override."""
    async with aiosqlite.connect(settings.db_path) as db:
        val_str = json.dumps(value)
        await db.execute(
            "INSERT INTO bot_settings (key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (key, val_str),
        )
        await db.commit()
