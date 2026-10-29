"""
Mingle2 Bot — Like-Back State Machine & Queue Engine
Enforces atomic state transitions, persistent 5-minute countdowns,
duplicate protection, and verified success detection.
"""

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from enum import Enum
import logging
from typing import Any, Dict, List, Optional, Tuple

from src.profile_identity import Profile, canonicalize_profile_url

logger = logging.getLogger("mingle2.like_state_machine")


class ActionState(str, Enum):
    # Success flow
    DETECTED = "DETECTED"
    IDENTITY_VERIFIED = "IDENTITY_VERIFIED"
    QUEUED = "QUEUED"
    WAITING = "WAITING"
    READY = "READY"
    EXECUTING = "EXECUTING"
    VERIFYING = "VERIFYING"
    COMPLETED = "COMPLETED"

    # Failure / terminal states
    IDENTITY_MISMATCH = "IDENTITY_MISMATCH"
    IDENTITY_FAILED = "IDENTITY_FAILED"
    EXECUTION_FAILED = "EXECUTION_FAILED"
    VERIFICATION_FAILED = "VERIFICATION_FAILED"
    SESSION_EXPIRED = "SESSION_EXPIRED"
    RATE_LIMITED = "RATE_LIMITED"
    TEMPORARY_ERROR = "TEMPORARY_ERROR"
    PERMANENT_ERROR = "PERMANENT_ERROR"
    SKIPPED = "SKIPPED"
    UNKNOWN = "UNKNOWN"


class VerificationStatus(str, Enum):
    UNVERIFIED = "UNVERIFIED"
    PENDING_VERIFICATION = "PENDING_VERIFICATION"
    VERIFIED = "VERIFIED"
    MISMATCH = "MISMATCH"
    CANNOT_VERIFY = "CANNOT_VERIFY"


class MatchStatus(str, Enum):
    MATCH_CONFIRMED = "MATCH CONFIRMED"
    UNVERIFIED = "UNVERIFIED"


# Valid state machine transitions
ALLOWED_TRANSITIONS = {
    ActionState.DETECTED: {
        ActionState.IDENTITY_VERIFIED,
        ActionState.IDENTITY_MISMATCH,
        ActionState.IDENTITY_FAILED,
        ActionState.SKIPPED,
    },
    ActionState.IDENTITY_VERIFIED: {
        ActionState.QUEUED,
        ActionState.WAITING,
        ActionState.READY,
        ActionState.SKIPPED,
    },
    ActionState.QUEUED: {
        ActionState.WAITING,
        ActionState.READY,
        ActionState.SKIPPED,
    },
    ActionState.WAITING: {
        ActionState.READY,
        ActionState.EXECUTING,
        ActionState.SKIPPED,
    },
    ActionState.READY: {
        ActionState.EXECUTING,
        ActionState.SKIPPED,
        ActionState.WAITING,  # If delayed
    },
    ActionState.EXECUTING: {
        ActionState.VERIFYING,
        ActionState.IDENTITY_MISMATCH,
        ActionState.EXECUTION_FAILED,
        ActionState.TEMPORARY_ERROR,
        ActionState.SESSION_EXPIRED,
        ActionState.RATE_LIMITED,
        ActionState.PERMANENT_ERROR,
        ActionState.UNKNOWN,
    },
    ActionState.VERIFYING: {
        ActionState.COMPLETED,
        ActionState.VERIFICATION_FAILED,
        ActionState.IDENTITY_MISMATCH,
        ActionState.TEMPORARY_ERROR,
        ActionState.RATE_LIMITED,
        ActionState.PERMANENT_ERROR,
        ActionState.UNKNOWN,
    },
    # Retry flows from temporary errors
    ActionState.TEMPORARY_ERROR: {
        ActionState.WAITING,
        ActionState.READY,
        ActionState.EXECUTING,
        ActionState.PERMANENT_ERROR,
        ActionState.SKIPPED,
    },
    ActionState.EXECUTION_FAILED: {
        ActionState.WAITING,
        ActionState.READY,
        ActionState.PERMANENT_ERROR,
        ActionState.SKIPPED,
    },
    ActionState.VERIFICATION_FAILED: {
        ActionState.WAITING,
        ActionState.READY,
        ActionState.PERMANENT_ERROR,
        ActionState.SKIPPED,
    },
    ActionState.RATE_LIMITED: {
        ActionState.WAITING,
        ActionState.READY,
        ActionState.SKIPPED,
    },
    # Terminal states
    ActionState.COMPLETED: set(),
    ActionState.PERMANENT_ERROR: set(),
    ActionState.IDENTITY_FAILED: set(),
    ActionState.IDENTITY_MISMATCH: set(),
    ActionState.SESSION_EXPIRED: {ActionState.WAITING, ActionState.READY},  # Can resume after login
    ActionState.SKIPPED: set(),
    ActionState.UNKNOWN: set(),
}


@dataclass
class QueueItem:
    """
    Representation of an item in the persistent automation queue.
    """
    id: Optional[int] = None
    profile_id: str = ""
    username: str = "Unknown"
    profile_url: str = ""
    avatar_img_url: Optional[str] = None
    age_location: str = ""
    action_type: str = "LIKE_BACK"
    status: ActionState = ActionState.DETECTED
    detected_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    scheduled_like_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    started_at: Optional[str] = None
    completed_at: Optional[str] = None
    attempt_count: int = 0
    max_attempts: int = 3
    verification_status: VerificationStatus = VerificationStatus.UNVERIFIED
    verification_details: Optional[str] = None
    match_status: str = "UNVERIFIED"
    error_message: Optional[str] = None
    is_test_mode: bool = False
    created_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    updated_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())

    @property
    def display_name(self) -> str:
        return self.username

    @property
    def scheduled_at(self) -> str:
        return self.scheduled_like_at

    @property
    def remaining_seconds(self) -> float:
        """Calculate real-time remaining seconds without modifying persistent schedule."""
        try:
            sched = datetime.fromisoformat(self.scheduled_like_at)
            now = datetime.now(timezone.utc)
            delta = (sched - now).total_seconds()
            return max(0.0, delta)
        except Exception:
            return 0.0

    @property
    def is_ready_for_execution(self) -> bool:
        """True if the item is in WAITING or READY state and the countdown has elapsed."""
        if self.status in (ActionState.WAITING, ActionState.READY, ActionState.QUEUED):
            return self.remaining_seconds <= 0.0
        return False

    def can_transition_to(self, new_state: ActionState) -> bool:
        """Validates if transition to new_state is allowed by state machine."""
        allowed = ALLOWED_TRANSITIONS.get(self.status, set())
        return new_state in allowed

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "profile_id": self.profile_id,
            "display_name": self.username,
            "username": self.username,
            "profile_url": self.profile_url,
            "avatar_img_url": self.avatar_img_url,
            "age_location": self.age_location,
            "action_type": self.action_type,
            "status": self.status.value if isinstance(self.status, ActionState) else str(self.status),
            "detected_at": self.detected_at,
            "scheduled_at": self.scheduled_like_at,
            "scheduled_like_at": self.scheduled_like_at,
            "started_at": self.started_at,
            "completed_at": self.completed_at,
            "attempt_count": self.attempt_count,
            "max_attempts": self.max_attempts,
            "verification_status": self.verification_status.value if isinstance(self.verification_status, VerificationStatus) else str(self.verification_status),
            "verification_details": self.verification_details,
            "match_status": self.match_status,
            "error_message": self.error_message,
            "is_test_mode": self.is_test_mode,
            "remaining_seconds": round(self.remaining_seconds, 1),
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }
