"""
Mingle2 Bot — Comprehensive Audit Fix Verification Test Suite
Tests:
1. Profile identity resolution & verification (prevents wrong profile bug)
2. Queue persistence & independent 5-minute timers across restarts
3. Duplicate protection (prevents already queued/liked profiles from re-entering)
4. Like-back state machine transitions & Test Mode simulation
5. Deep profession search taxonomy & explainable 4-factor ranking
"""

import asyncio
from datetime import datetime, timedelta, timezone
import sys
from pathlib import Path

# Add project root to sys.path
BASE_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE_DIR))

from src.config import settings
from src.profile_identity import (
    Profile,
    canonicalize_profile_url,
    resolve_profile_from_raw_card,
    verify_profile_identity,
    ProfileIdentityMismatch,
    ProfileRedirectRejected,
)
from src.like_state_machine import (
    ActionState,
    VerificationStatus,
    QueueItem,
    ALLOWED_TRANSITIONS,
)
from src.search_engine import (
    PROFESSION_TAXONOMY,
    calculate_match_scores,
    rank_and_filter_search_results,
)
from src.bot_engine import (
    verify_like_back_response,
    strip_google_vignette,
)
from src import database
import tempfile
from contextlib import contextmanager

@contextmanager
def assert_raises(exc_type):
    try:
        yield
    except exc_type:
        return
    except Exception as e:
        raise AssertionError(f"Expected {exc_type.__name__}, got {type(e).__name__}: {e}")
    raise AssertionError(f"Expected {exc_type.__name__}, but no exception was raised")


# ============================================================================
# 1. PROFILE IDENTITY MODEL & WRONG PROFILE PREVENTION
# ============================================================================
def test_canonicalize_profile_url():
    """Verify canonical URL formatting strips extraneous parameters and normalizes."""
    # Standard profile view
    url, uid = canonicalize_profile_url("https://mingle2.com/user/view/123456")
    assert uid == "123456"
    assert url == "https://mingle2.com/users/view/123456"

    # Profile view with query params and hash
    url, uid = canonicalize_profile_url("https://mingle2.com/users/view/abc_789?ref=inbox#google_vignette")
    assert uid == "abc_789"
    assert url == "https://mingle2.com/users/view/abc_789"

    # Mutual match candidate URL
    url, uid = canonicalize_profile_url("https://mingle2.com/mutual_match/match/555123")
    assert uid == "555123"
    assert url == "https://mingle2.com/users/view/555123"

    # Invalid URL returns None
    url, uid = canonicalize_profile_url("https://mingle2.com/about_us")
    assert uid is None


def test_resolve_raw_card_paywalled_preview():
    """
    ROOT CAUSE FIX TEST:
    Cards with data-rating-id under paywall (blur-photo) MUST NOT treat rating-id as user ID.
    Must be resolved as is_paywalled_preview=True with who_is_interested_in_me URL.
    """
    card = {
        "user_id": None,
        "username": "Mystery Fan",
        "user_link_href": None,
        "is_blur": True,
        "data_rating_id": "10570551",  # Internal rating ID
        "data_button_id": "btn_42",
        "avatar": "https://mingle2.com/photos/blurred.jpg",
        "age_location": "28 • Miami, FL",
    }
    profile = resolve_profile_from_raw_card(card)
    assert profile.is_paywalled_preview is True
    # Crucial: ID must NOT be '10570551' directly, and URL must NOT be /users/view/10570551
    assert "10570551" not in profile.profile_url
    assert profile.profile_url == "https://mingle2.com/mutual_match/who_is_interested_in_me"
    assert profile.platform_profile_id.startswith("fan_preview_")


def test_resolve_raw_card_unblurred_real_user():
    """Real unblurred cards with /users/view/UID link must resolve to canonical user profile."""
    card = {
        "user_id": "987654",
        "username": "Sarah Jenkins",
        "user_link_href": "https://mingle2.com/users/view/987654",
        "is_blur": False,
        "data_rating_id": "10570551",
        "avatar": "https://mingle2.com/photos/sarah.jpg",
        "age_location": "29 • New York, NY",
    }
    profile = resolve_profile_from_raw_card(card)
    assert profile.is_paywalled_preview is False
    assert profile.platform_profile_id == "987654"
    assert profile.profile_url == "https://mingle2.com/users/view/987654"
    assert profile.display_name == "Sarah Jenkins"


def test_verify_profile_identity_mismatch():
    """Verify that identity mismatch between expected ID and actual loaded page is caught."""
    # Expected ID is 111, but loaded page is for member 222
    with assert_raises(ProfileIdentityMismatch):
        verify_profile_identity(
            expected_profile="111",
            actual_url="https://mingle2.com/users/view/222",
            loaded_username="Wrong Person",
            raise_on_mismatch=True,
        )

    # Redirected to deactivated profile
    with assert_raises(ProfileRedirectRejected):
        verify_profile_identity(
            expected_profile="111",
            actual_url="https://mingle2.com/user/deactivated",
            loaded_username="",
            raise_on_mismatch=True,
        )

    # Matching ID succeeds
    verified, _ = verify_profile_identity(
        expected_profile="111",
        actual_url="https://mingle2.com/users/view/111",
        loaded_username="Alice",
    )
    assert verified is True
    assert verified is True


# ============================================================================
# 2. PERSISTENT 5-MINUTE QUEUE & INDEPENDENT TIMERS ACROSS RESTARTS
# ============================================================================
async def test_queue_persistence_and_restart():
    """
    Verify:
    1. Independent 5-minute countdown per profile is saved to SQLite.
    2. Simulated application restart retains original scheduled_like_at without resetting.
    3. Elapsed countdowns are accurately detected by get_pending_likes.
    """
    # Use temporary test database
    temp_dir = tempfile.TemporaryDirectory()
    test_db = Path(temp_dir.name) / "test_queue.db"
    orig_db_path = settings.db_path
    settings.db_path = test_db

    try:
        await database.init_db()

        now = datetime.now(timezone.utc)
        # Profile 1: scheduled 5 minutes (300s) in the future
        sched_future = now + timedelta(seconds=300)
        p1, is_new1 = await database.add_or_get_liker(
            user_id="user_future",
            username="FutureUser",
            profile_url="https://mingle2.com/users/view/user_future",
            avatar_img_url=None,
            age_location="25, Dallas",
            detected_at=now,
            scheduled_like_at=sched_future,
            initial_status="WAITING",
        )
        assert is_new1 is True
        assert p1["remaining_seconds"] > 250

        # Profile 2: detected earlier, scheduled 10 seconds in past (ready for execution)
        sched_past = now - timedelta(seconds=10)
        p2, is_new2 = await database.add_or_get_liker(
            user_id="user_past",
            username="ReadyUser",
            profile_url="https://mingle2.com/users/view/user_past",
            avatar_img_url=None,
            age_location="30, Seattle",
            detected_at=now - timedelta(seconds=310),
            scheduled_like_at=sched_past,
            initial_status="WAITING",
        )
        assert is_new2 is True
        assert p2["remaining_seconds"] == 0.0

        # SIMULATE APP RESTART: Re-query from DB without passing in-memory state
        ready_items = await database.get_pending_likes(before_time=now)
        ready_ids = [item["user_id"] for item in ready_items]

        # Only user_past should be returned as ready; user_future must still be waiting!
        assert "user_past" in ready_ids
        assert "user_future" not in ready_ids

        # Verify user_future schedule was NOT reset to a new 5 minutes
        p1_reloaded = await database.get_like_by_user_id("user_future")
        assert p1_reloaded["scheduled_like_at"] == sched_future.isoformat()
        assert p1_reloaded["status"] == "WAITING"

    finally:
        settings.db_path = orig_db_path
        temp_dir.cleanup()


# ============================================================================
# 3. DUPLICATE PROTECTION
# ============================================================================
async def test_duplicate_protection():
    """Verify that adding an already queued or completed profile does not duplicate or reset schedule."""
    temp_dir = tempfile.TemporaryDirectory()
    test_db = Path(temp_dir.name) / "test_dedup.db"
    orig_db_path = settings.db_path
    settings.db_path = test_db

    try:
        await database.init_db()
        now = datetime.now(timezone.utc)
        sched = now + timedelta(seconds=300)

        # First ingestion
        _, is_new = await database.add_or_get_liker(
            user_id="user_unique_101",
            username="UniqueMember",
            profile_url="https://mingle2.com/users/view/user_unique_101",
            avatar_img_url=None,
            age_location="27, Chicago",
            detected_at=now,
            scheduled_like_at=sched,
            initial_status="WAITING",
        )
        assert is_new is True

        # Second ingestion with different time (e.g. repeated scan 1 minute later)
        later_sched = now + timedelta(seconds=600)
        re_item, is_new2 = await database.add_or_get_liker(
            user_id="user_unique_101",
            username="UniqueMember",
            profile_url="https://mingle2.com/users/view/user_unique_101",
            avatar_img_url=None,
            age_location="27, Chicago",
            detected_at=now + timedelta(seconds=60),
            scheduled_like_at=later_sched,
        )
        assert is_new2 is False
        # Scheduled time must retain original schedule, not updated
        assert re_item["scheduled_like_at"] == sched.isoformat()

        # Mark item as COMPLETED
        await database.update_status("user_unique_101", "COMPLETED")

        # Third ingestion must still recognize existing item and not create duplicate
        re_item3, is_new3 = await database.add_or_get_liker(
            user_id="user_unique_101",
            username="UniqueMember",
            profile_url="https://mingle2.com/users/view/user_unique_101",
            avatar_img_url=None,
            age_location="27, Chicago",
            detected_at=now + timedelta(seconds=120),
            scheduled_like_at=later_sched,
        )
        assert is_new3 is False
        assert re_item3["status"] == "COMPLETED"

    finally:
        settings.db_path = orig_db_path
        temp_dir.cleanup()


# ============================================================================
# 4. LIKE-BACK STATE MACHINE
# ============================================================================
def test_state_machine_transitions():
    """Verify atomic state machine transitions and rejection of invalid states."""
    item = QueueItem(
        profile_id="test_44",
        status=ActionState.DETECTED,
    )

    # Allowed: DETECTED -> IDENTITY_VERIFIED
    assert item.can_transition_to(ActionState.IDENTITY_VERIFIED) is True
    # Disallowed: DETECTED -> COMPLETED (cannot jump directly to completed)
    assert item.can_transition_to(ActionState.COMPLETED) is False

    # Simulate flow
    item.status = ActionState.IDENTITY_VERIFIED
    assert item.can_transition_to(ActionState.WAITING) is True

    item.status = ActionState.WAITING
    assert item.can_transition_to(ActionState.READY) is True
    assert item.can_transition_to(ActionState.EXECUTING) is True

    item.status = ActionState.EXECUTING
    assert item.can_transition_to(ActionState.VERIFYING) is True
    assert item.can_transition_to(ActionState.EXECUTION_FAILED) is True

    item.status = ActionState.VERIFYING
    assert item.can_transition_to(ActionState.COMPLETED) is True
    assert item.can_transition_to(ActionState.VERIFICATION_FAILED) is True

    item.status = ActionState.COMPLETED
    # Terminal state: cannot transition to any other state
    assert item.can_transition_to(ActionState.WAITING) is False


# ============================================================================
# 5. DEEP PROFESSION SEARCH & EXPLAINABLE RANKING
# ============================================================================
def test_profession_taxonomy_expansion():
    """
    Verify:
    1. 'Nurse' expands to 'Registered Nurse' (high score 98%), 'Pediatric Nurse' (95%), 'Nursing Student' (82%)
    2. Completely unrelated professions receive 0% profession score
    """
    # Registered nurse exact alias
    _, prof_rn, _, _, _, _, matched_rn = calculate_match_scores(
        {"headline": "Registered Nurse at city hospital"},
        query_profession="Nurse",
    )
    assert prof_rn >= 90
    assert "registered nurse" in matched_rn.lower()

    # Direct keyword
    _, prof_nurse, _, _, _, _, _ = calculate_match_scores(
        {"about_me": "Pediatric Nurse loving life"},
        query_profession="Nurse",
    )
    assert prof_nurse >= 80

    # Student / assistant
    _, prof_student, _, _, _, _, _ = calculate_match_scores(
        {"about_me": "Nursing Student graduating soon"},
        query_profession="Nurse",
    )
    assert 70 <= prof_student <= 90

    # Software Engineer vs Nurse
    _, prof_unrelated, _, _, _, _, _ = calculate_match_scores(
        {"about_me": "Software Engineer writing code"},
        query_profession="Nurse",
    )
    assert prof_unrelated == 0


def test_explainable_ranking_order():
    """Verify profiles are ranked deterministically with explainable score breakdowns."""
    candidates = [
        {
            "user_id": "c1",
            "username": "UnrelatedAlex",
            "age_location": "28, Atlanta",
            "headline": "Retail Associate",
            "about_me": "Love movies",
        },
        {
            "user_id": "c2",
            "username": "NurseSarah",
            "age_location": "26, New York, NY",
            "headline": "Registered Nurse",
            "about_me": "Compassionate ICU RN loving travel",
        },
        {
            "user_id": "c3",
            "username": "NurseStudentEmma",
            "age_location": "24, New York, NY",
            "headline": "Nursing Student",
            "about_me": "Studying nursing at NYU",
        },
    ]

    # Query with profession="Nurse" filters out unrelated and ranks #1 NurseSarah then #2 NurseStudentEmma
    ranked = rank_and_filter_search_results(
        candidates=candidates,
        query_profession="Nurse",
        query_location="New York",
    )

    assert len(ranked) == 2
    # NurseSarah should be #1 (Registered Nurse 98% + New York 100%)
    assert ranked[0].platform_profile_id == "c2"
    assert ranked[0].display_name == "NurseSarah"
    assert ranked[0].total_match_score > 70
    assert ranked[0].profession_score >= 90
    assert ranked[0].location_score >= 80
    assert "Profession" in ranked[0].score_breakdown

    # NurseStudentEmma should be #2 (Nursing Student 82% + New York 100%)
    assert ranked[1].platform_profile_id == "c3"
    assert ranked[1].total_match_score <= ranked[0].total_match_score


def test_strip_google_vignette_directive():
    """Verify that #google_vignette hash fragments are stripped cleanly from URLs."""
    raw_url = "https://mingle2.com/mutual_match/who_is_interested_in_me#google_vignette"
    clean = strip_google_vignette(raw_url)
    assert clean == "https://mingle2.com/mutual_match/who_is_interested_in_me"

    user_url = "https://mingle2.com/users/view/1234567#google_vignette"
    clean_user = strip_google_vignette(user_url)
    assert clean_user == "https://mingle2.com/users/view/1234567"

    assert strip_google_vignette("") == ""
    assert strip_google_vignette("https://mingle2.com/user/view/999") == "https://mingle2.com/user/view/999"


def test_verify_like_back_response_directive():
    """Verify explicit server payload validation for like-back and match confirmation."""
    # 1. Success with match
    res_match = verify_like_back_response('{"status":"success","is_match":true,"message":"It\'s a match!"}')
    assert res_match["action_applied"] is True
    assert res_match["is_match"] is True

    # 2. Success without match
    res_like = verify_like_back_response('{"status":"success","rating":"liked"}')
    assert res_like["action_applied"] is True
    assert res_like["is_match"] is False

    # 3. Explicit error / already liked
    res_already = verify_like_back_response('{"status":"error","code":"already_liked"}')
    assert res_already["action_applied"] is False
    assert res_already["is_match"] is False

    # 4. Rate limit
    res_rl = verify_like_back_response('{"error":"rate_limited"}')
    assert res_rl["action_applied"] is False

    # 5. Empty or unexpected payload
    res_empty = verify_like_back_response('')
    assert res_empty["action_applied"] is False


def run_all():
    print("=" * 65)
    print("   RUNNING MINGLE2 AUDIT FIXES & SYSTEM VERIFICATION SUITE")
    print("=" * 65)

    tests_sync = [
        ("Canonicalize Profile URL", test_canonicalize_profile_url),
        ("Resolve Paywalled Fan Preview", test_resolve_raw_card_paywalled_preview),
        ("Resolve Unblurred Real User", test_resolve_raw_card_unblurred_real_user),
        ("Verify Profile Identity Mismatch Detection", test_verify_profile_identity_mismatch),
        ("State Machine Atomic Transitions", test_state_machine_transitions),
        ("Deep Profession Taxonomy Expansion (Nurse -> RN)", test_profession_taxonomy_expansion),
        ("Explainable 4-Factor Ranking Order", test_explainable_ranking_order),
        ("Strip Google Vignette Directive", test_strip_google_vignette_directive),
        ("Verify Like Back Response Payload Directive", test_verify_like_back_response_directive),
    ]

    tests_async = [
        ("Persistent 5-Minute Queue & Restart Timers", test_queue_persistence_and_restart),
        ("Queue Duplicate Protection & Re-ingestion Prevention", test_duplicate_protection),
    ]

    passed = 0
    total = len(tests_sync) + len(tests_async)

    for name, fn in tests_sync:
        try:
            fn()
            print(f" [PASS] {name}")
            passed += 1
        except Exception as e:
            print(f" [FAIL] {name}: {e}")
            import traceback
            traceback.print_exc()

    for name, fn in tests_async:
        try:
            asyncio.run(fn())
            print(f" [PASS] {name}")
            passed += 1
        except Exception as e:
            print(f" [FAIL] {name}: {e}")
            import traceback
            traceback.print_exc()

    print("=" * 65)
    print(f"Results: {passed}/{total} tests passed ({passed/total*100:.1f}%)")
    print("=" * 65)
    if passed != total:
        sys.exit(1)


if __name__ == "__main__":
    import sys
    run_all()

