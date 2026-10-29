"""
Mingle2 Bot — Session State Machine
Defines the authoritative session states, valid transitions, and helper utilities.
"""

from enum import Enum
import logging
from typing import Set

logger = logging.getLogger("mingle2.session_state")


class SessionState(str, Enum):
    LOGIN_REQUIRED = "LOGIN_REQUIRED"
    OPENING_BROWSER = "OPENING_BROWSER"
    WAITING_FOR_LOGIN = "WAITING_FOR_LOGIN"
    LOGIN_DETECTED = "LOGIN_DETECTED"
    SESSION_VERIFIED = "SESSION_VERIFIED"
    CONNECTED = "CONNECTED"
    SESSION_EXPIRED = "SESSION_EXPIRED"
    ERROR = "ERROR"


# Valid state machine transitions
ALLOWED_SESSION_TRANSITIONS = {
    SessionState.LOGIN_REQUIRED: {
        SessionState.OPENING_BROWSER,
        SessionState.ERROR,
    },
    SessionState.OPENING_BROWSER: {
        SessionState.WAITING_FOR_LOGIN,
        SessionState.LOGIN_REQUIRED,
        SessionState.ERROR,
    },
    SessionState.WAITING_FOR_LOGIN: {
        SessionState.LOGIN_DETECTED,
        SessionState.LOGIN_REQUIRED,  # if user closes browser or times out
        SessionState.ERROR,
    },
    SessionState.LOGIN_DETECTED: {
        SessionState.SESSION_VERIFIED,
        SessionState.LOGIN_REQUIRED,
        SessionState.ERROR,
    },
    SessionState.SESSION_VERIFIED: {
        SessionState.CONNECTED,
        SessionState.SESSION_EXPIRED,
        SessionState.LOGIN_REQUIRED,
        SessionState.ERROR,
    },
    SessionState.CONNECTED: {
        SessionState.SESSION_EXPIRED,
        SessionState.SESSION_VERIFIED,  # periodic re-verification
        SessionState.LOGIN_REQUIRED,
        SessionState.OPENING_BROWSER,
        SessionState.ERROR,
    },
    SessionState.SESSION_EXPIRED: {
        SessionState.OPENING_BROWSER,
        SessionState.LOGIN_REQUIRED,
        SessionState.ERROR,
    },
    SessionState.ERROR: {
        SessionState.OPENING_BROWSER,
        SessionState.LOGIN_REQUIRED,
        SessionState.SESSION_EXPIRED,
    },
}


def can_transition_session(current: SessionState, target: SessionState) -> bool:
    """Validates if transition from current to target session state is permissible."""
    allowed = ALLOWED_SESSION_TRANSITIONS.get(current, set())
    return target in allowed


def get_connection_display_status(state: SessionState) -> str:
    """
    Returns user-facing connection badge:
    CONNECTED | LOGIN REQUIRED | SESSION EXPIRED
    """
    if state in (SessionState.CONNECTED, SessionState.SESSION_VERIFIED):
        return "CONNECTED"
    if state == SessionState.SESSION_EXPIRED:
        return "SESSION EXPIRED"
    return "LOGIN REQUIRED"


def is_session_authenticated(state: SessionState) -> bool:
    """Returns True if the session state implies an authenticated session."""
    return state in (SessionState.CONNECTED, SessionState.SESSION_VERIFIED)
