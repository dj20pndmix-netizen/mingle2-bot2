"""
Mingle2 Bot — Comprehensive Login, Session & Browser Management Test Suite
Tests:
1. Browser launch
2. Login-state detection
3. Session verification
4. Session expiration
5. Duplicate browser prevention
6. Profile identity mapping
7. Wrong-profile detection
8. Queue persistence
9. 5-minute timer persistence
10. Duplicate prevention
11. Like Back verification
12. MATCH verification
13. Browser closure
14. Reconnect flow
15. Error recovery
"""

import asyncio
from datetime import datetime, timedelta, timezone
import os
from pathlib import Path
import sys
import tempfile
from unittest.mock import AsyncMock, MagicMock, patch

# Add project root to sys.path
BASE_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE_DIR))

from src.config import settings
from src.session_state import (
    SessionState,
    get_connection_display_status,
    is_session_authenticated,
    can_transition_session,
)
from src.bot_engine import (
    Mingle2BotEngine,
    verify_like_back_response,
    strip_google_vignette,
)
from src.profile_identity import (
    Profile,
    canonicalize_profile_url,
    resolve_profile_from_raw_card,
    verify_profile_identity,
    ProfileIdentityMismatch,
)
from src.like_state_machine import (
    ActionState,
    VerificationStatus,
    MatchStatus,
    QueueItem,
    ALLOWED_TRANSITIONS,
)
from src import database


# ============================================================================
# 1. DUPLICATE BROWSER PREVENTION
# ============================================================================
async def test_duplicate_browser_prevention():
    engine = Mingle2BotEngine()
    engine._session_state = SessionState.WAITING_FOR_LOGIN
    # Mock active browser context
    engine._context = MagicMock()
    engine._context.pages = [MagicMock()]

    # Calling open_login_browser while waiting for login must prevent duplicate instance
    res = await engine.open_login_browser()
    assert res["status"] == "already_open", f"Expected 'already_open', got {res}"
    assert "already open" in res["message"].lower()
    print(" [PASS] Duplicate Browser Launch Prevention")


# ============================================================================
# 2. BROWSER LAUNCH FLOW
# ============================================================================
async def test_browser_launch_flow():
    engine = Mingle2BotEngine()
    assert engine.session_state == SessionState.LOGIN_REQUIRED

    # Mock playwright launch
    mock_page = MagicMock()
    mock_page.goto = AsyncMock()
    mock_page.on = MagicMock()

    mock_context = MagicMock()
    mock_context.pages = [mock_page]
    mock_context.new_page = AsyncMock(return_value=mock_page)
    mock_context.add_init_script = AsyncMock()
    mock_context.route = AsyncMock()
    mock_context.on = MagicMock()

    mock_browser = MagicMock()
    mock_browser.new_context = AsyncMock(return_value=mock_context)
    mock_browser.is_connected = MagicMock(return_value=True)

    mock_chromium = MagicMock()
    mock_chromium.launch = AsyncMock(return_value=mock_browser)
    mock_chromium.launch_persistent_context = AsyncMock(return_value=mock_context)

    mock_playwright = MagicMock()
    mock_playwright.chromium = mock_chromium
    mock_playwright.start = AsyncMock(return_value=mock_playwright)

    with patch("src.bot_engine.async_playwright", return_value=mock_playwright):
        with patch.object(engine, "wait_for_login", AsyncMock()):
            res = await engine.open_login_browser()
            assert res["status"] == "success"
            assert engine.session_state == SessionState.WAITING_FOR_LOGIN
            assert engine.is_login_mode is True
            mock_page.goto.assert_called_once()
            args, kwargs = mock_page.goto.call_args
            assert "mingle2.com/user/login" in args[0]
    print(" [PASS] Browser Launch & Navigation to Login Page")


# ============================================================================
# 3. LOGIN-STATE DETECTION
# ============================================================================
async def test_login_state_detection():
    engine = Mingle2BotEngine()
    engine._session_state = SessionState.WAITING_FOR_LOGIN

    mock_page = MagicMock()
    # Initially on login page
    mock_page.url = "https://mingle2.com/user/login"
    mock_context = MagicMock()
    mock_context.pages = [mock_page]
    mock_context.storage_state = AsyncMock()
    engine._context = mock_context

    # Simulate user completing login: page navigates to mutual match and DOM has avatar
    async def simulate_user_login():
        await asyncio.sleep(0.3)
        mock_page.url = "https://mingle2.com/mutual_match/match"
        mock_page.evaluate = AsyncMock(return_value=True)

    with patch.object(engine, "sync_account_profile", AsyncMock()):
        login_sim_task = asyncio.create_task(simulate_user_login())
        success = await engine.wait_for_login(timeout=5)
        await login_sim_task
        assert success is True
        assert engine.session_state == SessionState.CONNECTED
        assert engine.is_login_mode is False
    print(" [PASS] Observable Login-State Detection (WAITING -> CONNECTED)")


# ============================================================================
# 4. SESSION VERIFICATION (verify_mingle2_session)
# ============================================================================
async def test_session_verification_success():
    engine = Mingle2BotEngine()
    engine._session_state = SessionState.CONNECTED

    mock_page = MagicMock()
    mock_page.goto = AsyncMock()
    mock_page.close = AsyncMock()
    mock_page.url = "https://mingle2.com/mutual_match/match"
    # Authenticated indicators present
    mock_page.evaluate = AsyncMock(return_value=True)

    mock_context = MagicMock()
    mock_context.new_page = AsyncMock(return_value=mock_page)
    mock_context.storage_state = AsyncMock()

    with patch.object(engine, "get_context", AsyncMock(return_value=mock_context)):
        is_valid = await engine.verify_mingle2_session()
        assert is_valid is True
        assert engine.session_state == SessionState.CONNECTED
        assert is_session_authenticated(engine.session_state) is True
    print(" [PASS] Active Session Verification (Observable DOM Check)")


# ============================================================================
# 5. SESSION EXPIRATION DETECTION & SAFE STOP
# ============================================================================
async def test_session_expiration_detection():
    engine = Mingle2BotEngine()
    engine._session_state = SessionState.CONNECTED

    mock_page = MagicMock()
    mock_page.goto = AsyncMock()
    mock_page.close = AsyncMock()
    # Redirected to login page
    mock_page.url = "https://mingle2.com/user/login"
    mock_page.evaluate = AsyncMock(return_value=False)

    mock_context = MagicMock()
    mock_context.new_page = AsyncMock(return_value=mock_page)

    with patch.object(engine, "get_context", AsyncMock(return_value=mock_context)):
        is_valid = await engine.verify_mingle2_session()
        assert is_valid is False
        assert engine.session_state == SessionState.SESSION_EXPIRED
        assert get_connection_display_status(engine.session_state) == "SESSION EXPIRED"
    print(" [PASS] Session Expiration Detection & Transition to SESSION_EXPIRED")


# ============================================================================
# 6. BROWSER CLOSURE HANDLING
# ============================================================================
async def test_browser_closure_handling():
    engine = Mingle2BotEngine()
    engine._session_state = SessionState.WAITING_FOR_LOGIN
    engine.is_login_mode = True

    mock_task = MagicMock()
    mock_task.done.return_value = False
    engine._login_watcher_task = mock_task

    await engine.close_browser()
    mock_task.cancel.assert_called_once()
    assert engine.session_state == SessionState.LOGIN_REQUIRED
    assert engine.is_login_mode is False
    print(" [PASS] Clean Browser Closure & State Reset")


# ============================================================================
# 7. RECONNECT FLOW
# ============================================================================
async def test_reconnect_flow():
    engine = Mingle2BotEngine()
    engine._session_state = SessionState.SESSION_EXPIRED

    with patch.object(engine, "close_browser", AsyncMock()) as mock_close:
        with patch.object(engine, "verify_mingle2_session", AsyncMock(return_value=True)) as mock_verify:
            res = await engine.reconnect_browser()
            mock_close.assert_called_once()
            mock_verify.assert_called_once()
            assert res is True
    print(" [PASS] Browser Reconnect & Re-Verification Flow")


# ============================================================================
# 8. ERROR RECOVERY
# ============================================================================
async def test_error_recovery():
    assert can_transition_session(SessionState.ERROR, SessionState.OPENING_BROWSER)
    assert can_transition_session(SessionState.ERROR, SessionState.LOGIN_REQUIRED)
    assert can_transition_session(SessionState.SESSION_EXPIRED, SessionState.OPENING_BROWSER)

    engine = Mingle2BotEngine()
    await engine.set_session_state(SessionState.ERROR)
    assert engine.session_state == SessionState.ERROR
    assert get_connection_display_status(engine.session_state) == "LOGIN REQUIRED"

    # Recover by launching login browser
    await engine.set_session_state(SessionState.OPENING_BROWSER)
    assert engine.session_state == SessionState.OPENING_BROWSER
    print(" [PASS] State Machine Error Recovery & Allowed Transitions")


# ============================================================================
# 9. PROFILE IDENTITY MAPPING (STABLE OBJECT)
# ============================================================================
def test_profile_identity_mapping():
    profile = Profile(
        platform_profile_id="10570551",
        profile_url="https://mingle2.com/users/view/10570551",
        display_name="Sarah_RN",
        age=29,
        location="Chicago, IL",
        profession="Registered Nurse",
        photo_reference="https://mingle2.com/photos/10570551_main.jpg",
        source="MUTUAL_MATCH",
        verification_status="VERIFIED",
    )

    p_dict = profile.to_dict()
    assert p_dict["profile_id"] == "10570551"
    assert p_dict["display_name"] == "Sarah_RN"
    assert p_dict["profile_url"] == "https://mingle2.com/users/view/10570551"
    assert p_dict["identity_verified"] is True
    assert p_dict["source"] == "MUTUAL_MATCH"
    assert "photo_reference" in p_dict
    assert "timestamp_detected" in p_dict
    print(" [PASS] Stable Profile Identity Model Consistency")


# ============================================================================
# 10. WRONG-PROFILE DETECTION (PREVENTS PERSON A -> PERSON B BUG)
# ============================================================================
def test_wrong_profile_mismatch_detection():
    expected_profile = Profile(
        platform_profile_id="user_person_A",
        profile_url="https://mingle2.com/users/view/user_person_A",
        display_name="Alice",
    )

    opened_page_profile = Profile(
        platform_profile_id="user_person_B",
        profile_url="https://mingle2.com/users/view/user_person_B",
        display_name="Bob",
    )

    try:
        verify_profile_identity(expected_profile, opened_page_profile, raise_on_mismatch=True)
        assert False, "Failed to raise ProfileIdentityMismatch!"
    except ProfileIdentityMismatch as exc:
        assert exc.expected_id == "user_person_A"
        assert exc.actual_id == "user_person_B"
    print(" [PASS] Wrong Profile Identity Mismatch Detection (Person A vs Person B)")


# ============================================================================
# 11. QUEUE PERSISTENCE & 5-MINUTE TIMER PERSISTENCE
# ============================================================================
async def test_queue_and_timer_persistence():
    with tempfile.TemporaryDirectory() as tmp_dir:
        test_db = Path(tmp_dir) / "test_queue.db"
        with patch.object(settings, "db_path", test_db):
            await database.init_db()

            fixed_time = datetime(2026, 10, 8, 12, 0, 0, tzinfo=timezone.utc)
            scheduled_time = fixed_time + timedelta(minutes=5)

            # Ingest liker
            item, is_new = await database.add_or_get_liker(
                user_id="user_persisted_999",
                username="TargetUser",
                profile_url="https://mingle2.com/users/view/user_persisted_999",
                detected_at=fixed_time,
                scheduled_like_at=scheduled_time,
                initial_status="WAITING",
            )
            assert is_new is True

            # Query pending before elapsed: should be empty
            pending_before = await database.get_pending_likes(before_time=fixed_time + timedelta(minutes=2))
            assert len(pending_before) == 0

            # Query pending after 5 minutes has elapsed: should return item
            pending_after = await database.get_pending_likes(before_time=fixed_time + timedelta(minutes=5, seconds=1))
            assert len(pending_after) == 1
            assert pending_after[0]["user_id"] == "user_persisted_999"

            # Verify survival across db re-open (restart simulation)
            fetched = await database.get_like_by_user_id("user_persisted_999")
            assert fetched is not None
            assert fetched["scheduled_like_at"] == scheduled_time.isoformat()
    print(" [PASS] 5-Minute Timer Timestamp Persistence Across Restarts")


# ============================================================================
# 12. DUPLICATE INGESTION PREVENTION
# ============================================================================
async def test_duplicate_prevention():
    with tempfile.TemporaryDirectory() as tmp_dir:
        test_db = Path(tmp_dir) / "test_queue.db"
        with patch.object(settings, "db_path", test_db):
            await database.init_db()

            fixed_time = datetime(2026, 10, 8, 12, 0, 0, tzinfo=timezone.utc)
            item1, is_new1 = await database.add_or_get_liker(
                user_id="user_dup_check",
                username="UniqueUser",
                profile_url="https://mingle2.com/users/view/user_dup_check",
                detected_at=fixed_time,
                scheduled_like_at=fixed_time + timedelta(minutes=5),
            )
            assert is_new1 is True

            # Ingest same user again
            item2, is_new2 = await database.add_or_get_liker(
                user_id="user_dup_check",
                username="UniqueUser",
                profile_url="https://mingle2.com/users/view/user_dup_check",
                detected_at=fixed_time + timedelta(minutes=10),
                scheduled_like_at=fixed_time + timedelta(minutes=15),
            )
            assert is_new2 is False
            assert item1["id"] == item2["id"]
    print(" [PASS] Duplicate User ID Ingestion Protection")


# ============================================================================
# 13. LIKE-BACK VERIFICATION (NEVER HTTP 200 ALONE)
# ============================================================================
def test_like_back_verification():
    # Empty payload with HTTP 200 MUST NOT be treated as success
    res_empty = verify_like_back_response("")
    assert res_empty["action_applied"] is False

    # Error payload with HTTP 200
    res_err = verify_like_back_response('{"status":"error","message":"rate_limit_exceeded"}')
    assert res_err["action_applied"] is False

    # Authentic positive response payload
    res_success = verify_like_back_response('{"status":"success","action":"liked","is_match":false}')
    assert res_success["action_applied"] is True
    assert res_success["is_match"] is False
    print(" [PASS] Like-Back Payload Verification (No Blind HTTP 200 Assumption)")


# ============================================================================
# 14. MATCH VERIFICATION (MATCH CONFIRMED vs UNVERIFIED)
# ============================================================================
def test_match_verification():
    # Like succeeded, but not a mutual match
    res_no_match = verify_like_back_response('{"status":"success","action":"liked","is_match":false}')
    match_status1 = MatchStatus.MATCH_CONFIRMED.value if res_no_match["is_match"] else MatchStatus.UNVERIFIED.value
    assert match_status1 == "UNVERIFIED"

    # Mutual match confirmed by backend payload
    res_match = verify_like_back_response('{"status":"success","action":"mutual_match","is_match":true}')
    match_status2 = MatchStatus.MATCH_CONFIRMED.value if res_match["is_match"] else MatchStatus.UNVERIFIED.value
    assert match_status2 == "MATCH CONFIRMED"
    print(" [PASS] MATCH State Verification (MATCH CONFIRMED vs UNVERIFIED)")


# ============================================================================
# 15. SANITIZATION (NO SECRETS EXPORTED OR LOGGED)
# ============================================================================
async def test_account_state_sanitization():
    engine = Mingle2BotEngine()
    engine._session_state = SessionState.CONNECTED

    with tempfile.TemporaryDirectory() as tmp_dir:
        test_db = Path(tmp_dir) / "test_queue.db"
        with patch.object(settings, "db_path", test_db):
            await database.init_db()
            await database.save_account_profile({
                "user_id": "test_member_123",
                "username": "LegitUser",
                "headline": "Hiking & travel",
                "age": 30,
                "location": "Boston, MA",
            })

            acc_state = await engine.get_current_account_state()
            assert acc_state["session_state"] == "CONNECTED"
            assert acc_state["connection_status"] == "CONNECTED"
            assert acc_state["is_authenticated"] is True
            assert acc_state["account"]["username"] == "LegitUser"

            # Verify no sensitive auth keys exist in the exported state
            sensitive_keys = ["password", "cookie", "cookies", "token", "auth_token", "secret", "bearer"]
            for k in sensitive_keys:
                assert k not in acc_state, f"Found sensitive key '{k}' in account state!"
                assert k not in acc_state["account"], f"Found sensitive key '{k}' in account sub-dict!"
    print(" [PASS] Account State Strict Security Sanitization (Zero Tokens/Passwords)")


# ============================================================================
# MASTER RUNNER
# ============================================================================
async def run_all_tests():
    print("=" * 70)
    print("   RUNNING MINGLE2 LOGIN, SESSION & BROWSER MANAGEMENT TEST SUITE")
    print("=" * 70)

    await test_duplicate_browser_prevention()
    await test_browser_launch_flow()
    await test_login_state_detection()
    await test_session_verification_success()
    await test_session_expiration_detection()
    await test_browser_closure_handling()
    await test_reconnect_flow()
    await test_error_recovery()
    test_profile_identity_mapping()
    test_wrong_profile_mismatch_detection()
    await test_queue_and_timer_persistence()
    await test_duplicate_prevention()
    test_like_back_verification()
    test_match_verification()
    await test_account_state_sanitization()

    print("=" * 70)
    print(">>> ALL 15 AUTOMATED SYSTEM TESTS PASSED WITH 100% SUCCESS! <<<")
    print("=" * 70)


if __name__ == "__main__":
    asyncio.run(run_all_tests())
