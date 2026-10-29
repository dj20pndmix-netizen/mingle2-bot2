"""
Mingle2 Bot — Profile Identity & Verification Engine
Provides normalized profile identity models, URL canonicalization,
and strict pre-action identity verification to prevent mismatched actions.
"""

from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
import json
import logging
import re
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import urlparse, urlunparse

logger = logging.getLogger("mingle2.profile_identity")

# Canonical Mingle2 host
CANONICAL_HOST = "mingle2.com"


class ProfileIdentityException(Exception):
    """Base exception for profile identity failures."""
    pass


class ProfileIdentityMismatch(ProfileIdentityException):
    """Raised when the resolved profile does not match the expected identity."""
    def __init__(self, expected_id: str, actual_id: str, reason: str):
        self.expected_id = expected_id
        self.actual_id = actual_id
        self.reason = reason
        super().__init__(
            f"PROFILE IDENTITY MISMATCH: Expected ID '{expected_id}' but resolved '{actual_id}'. Reason: {reason}. Action blocked."
        )


class ProfileRedirectRejected(ProfileIdentityException):
    """Raised when navigating to a profile produces an unexpected redirect (e.g. deactivated/login)."""
    def __init__(self, target_id: str, redirect_url: str):
        self.target_id = target_id
        self.redirect_url = redirect_url
        super().__init__(
            f"PROFILE REDIRECT REJECTED: Profile ID '{target_id}' redirected to '{redirect_url}'. Action blocked."
        )


@dataclass
class Profile:
    """
    Normalized Profile Entity
    Enforces stable platform identity and prevents ambiguous array/index mapping.
    """
    internal_id: Optional[int] = None
    platform_profile_id: str = ""
    profile_url: str = ""
    display_name: str = "Unknown"
    age: Optional[int] = None
    gender: Optional[str] = None
    location: str = ""
    profession: str = ""
    interests: List[str] = field(default_factory=list)
    headline: str = ""
    about_me: str = ""
    photo_reference: Optional[str] = None
    source: str = "UNKNOWN"  # MUTUAL_MATCH, SEARCH, ONLINE, FAN_PREVIEW, TEST_SIMULATION
    is_paywalled_preview: bool = False
    verification_status: str = "UNVERIFIED"  # UNVERIFIED, VERIFIED, MISMATCH, DEACTIVATED, PAYWALLED
    discovered_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    last_verified_at: Optional[str] = None
    raw_metadata: Dict[str, Any] = field(default_factory=dict)

    @property
    def profile_id(self) -> str:
        return self.platform_profile_id

    @property
    def timestamp_detected(self) -> str:
        return self.discovered_at

    @property
    def identity_verified(self) -> bool:
        return self.verification_status == "VERIFIED"

    def to_dict(self) -> Dict[str, Any]:
        data = asdict(self)
        data["profile_id"] = self.platform_profile_id
        data["timestamp_detected"] = self.discovered_at
        data["identity_verified"] = self.identity_verified
        return data

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "Profile":
        interests = data.get("interests", [])
        if isinstance(interests, str):
            try:
                interests = json.loads(interests)
            except Exception:
                interests = [i.strip() for i in interests.split(",") if i.strip()]
        raw_meta = data.get("raw_metadata", {})
        if isinstance(raw_meta, str):
            try:
                raw_meta = json.loads(raw_meta)
            except Exception:
                raw_meta = {}

        platform_id = str(data.get("profile_id") or data.get("platform_profile_id", ""))
        verif_status = data.get("verification_status")
        if not verif_status and data.get("identity_verified") is True:
            verif_status = "VERIFIED"

        return cls(
            internal_id=data.get("internal_id") or data.get("id"),
            platform_profile_id=platform_id,
            profile_url=data.get("profile_url", ""),
            display_name=data.get("display_name") or data.get("username", "Unknown"),
            age=data.get("age"),
            gender=data.get("gender"),
            location=data.get("location", ""),
            profession=data.get("profession", ""),
            interests=interests,
            headline=data.get("headline", ""),
            about_me=data.get("about_me", ""),
            photo_reference=data.get("photo_reference") or data.get("avatar_img_url") or data.get("avatar_url"),
            source=data.get("source", "UNKNOWN"),
            is_paywalled_preview=bool(data.get("is_paywalled_preview", False)),
            verification_status=verif_status or "UNVERIFIED",
            discovered_at=data.get("timestamp_detected") or data.get("discovered_at") or datetime.now(timezone.utc).isoformat(),
            last_verified_at=data.get("last_verified_at"),
            raw_metadata=raw_meta,
        )


def canonicalize_profile_url(raw_url_or_id: str) -> Tuple[str, Optional[str]]:
    """
    Normalizes a profile URL or member ID to the canonical Mingle2 format:
    https://mingle2.com/users/view/{platform_profile_id}

    Returns:
        (canonical_url, platform_profile_id)
    """
    if not raw_url_or_id:
        return "", None

    raw_str = str(raw_url_or_id).strip()
    if "#google_vignette" in raw_str:
        raw_str = raw_str.split("#google_vignette")[0].rstrip("#")

    # If already a pure numeric or alphanumeric ID
    if re.fullmatch(r"[0-9]+", raw_str):
        profile_id = raw_str
        return f"https://mingle2.com/users/view/{profile_id}", profile_id

    # If it is a simulation ID
    if raw_str.startswith("sim_"):
        return f"https://mingle2.com/users/view/{raw_str}", raw_str

    # Extract ID from URLs like /users/view/123, /user/view/123, /mutual_match/match/123, /profile/123
    match = re.search(r"(?:users/view|user/view|mutual_match/match|mutual_match/rate|profile|mail_detail)/([a-zA-Z0-9_-]+)", raw_str, re.IGNORECASE)
    if match:
        profile_id = match.group(1)
        return f"https://mingle2.com/users/view/{profile_id}", profile_id

    # Parse full URL
    parsed = urlparse(raw_str)
    if not parsed.netloc and raw_str.startswith("/"):
        parsed = urlparse(f"https://mingle2.com{raw_str}")

    path_parts = [p for p in parsed.path.split("/") if p]
    if len(path_parts) >= 2 and path_parts[0] in ("user", "users") and path_parts[1] == "view":
        profile_id = path_parts[2] if len(path_parts) > 2 else None
        if profile_id:
            return f"https://mingle2.com/users/view/{profile_id}", profile_id

    return raw_str, None


def resolve_profile_from_raw_card(card_data: Dict[str, Any], source: str = "RAW_CARD") -> Profile:
    """
    Safely resolves a raw parsed card into a validated Profile entity.
    Prevents confusing data-rating-id with platform_profile_id on paywalled fan cards.
    """
    raw_uid = str(card_data.get("user_id") or card_data.get("id") or "").strip()
    raw_url = str(card_data.get("profile_url") or card_data.get("href") or "").strip()
    name = str(card_data.get("username") or card_data.get("name") or "Unknown").strip()
    avatar = card_data.get("avatar_img_url") or card_data.get("avatar") or card_data.get("avatar_url")
    age_location = str(card_data.get("age_location") or card_data.get("details") or "").strip()

    age = None
    age_match = re.search(r"(?:Age:?\s*|,\s*|^)(\d{2})(?:\s*•|,|$|\s)", age_location, re.IGNORECASE)
    if age_match:
        try:
            age = int(age_match.group(1))
        except Exception:
            age = None

    gender = None
    if "woman" in age_location.lower() or "female" in age_location.lower():
        gender = "Female"
    elif "man" in age_location.lower() or "male" in age_location.lower():
        gender = "Male"

    # Detect if this is an obscured paywall preview card from who_is_interested_in_me
    is_fan_preview = (
        bool(card_data.get("is_fan_preview")) or
        bool(card_data.get("is_blur")) or
        (card_data.get("data_rating_id") is not None and not card_data.get("user_link_href") and not raw_uid) or
        "blur-photo" in str(card_data.get("className", "")) or
        "/payment" in raw_url or
        "javascript:void" in raw_url or
        raw_uid.startswith("fan_")
    )

    if is_fan_preview:
        rating_id = str(card_data.get("data_rating_id") or card_data.get("data-rating-id") or raw_uid or "").replace("fan_", "")
        platform_id = f"fan_preview_{rating_id}" if rating_id else f"fan_preview_{int(datetime.now().timestamp())}"
        canonical_url = "https://mingle2.com/mutual_match/who_is_interested_in_me"
        return Profile(
            platform_profile_id=platform_id,
            profile_url=canonical_url,
            display_name=name,
            age=age,
            gender=gender,
            location=age_location,
            photo_reference=avatar,
            source="FAN_PREVIEW",
            is_paywalled_preview=True,
            verification_status="PAYWALLED_PREVIEW",
            raw_metadata=card_data,
        )

    # Standard cards: canonicalize URL & extract platform ID
    canonical_url, platform_id = canonicalize_profile_url(raw_url or raw_uid)
    if not platform_id and raw_uid:
        platform_id = raw_uid
        canonical_url = f"https://mingle2.com/users/view/{platform_id}"

    if not platform_id:
        platform_id = f"unknown_{int(datetime.now().timestamp())}"

    return Profile(
        platform_profile_id=platform_id,
        profile_url=canonical_url,
        display_name=name,
        age=age,
        gender=gender,
        location=age_location,
        photo_reference=avatar,
        source=source,
        is_paywalled_preview=False,
        verification_status="UNVERIFIED",
        raw_metadata=card_data,
    )


def verify_profile_identity(
    expected_profile: Union[Profile, str],
    actual_url: Union[str, Profile],
    actual_title: Optional[str] = None,
    actual_dom_data: Optional[Dict[str, Any]] = None,
    loaded_username: Optional[str] = None,
    raise_on_mismatch: bool = False,
) -> Tuple[bool, str]:
    """
    Strictly verifies that a loaded Mingle2 page corresponds to expected profile identity.

    Rules:
    1. Detect unexpected redirects (e.g. redirected to /user/deactivated, /user/login, or home).
    2. Normalize URL and match platform_profile_id in path.
    3. If DOM data is available, verify username does not conflict drastically.
    4. Reject any mismatch (returns (False, reason), or raises if raise_on_mismatch=True).
    """
    if isinstance(expected_profile, str):
        expected_profile = Profile(platform_profile_id=expected_profile)

    if isinstance(actual_url, Profile):
        if loaded_username is None:
            loaded_username = actual_url.display_name
        norm_actual_url = (actual_url.profile_url or f"https://mingle2.com/users/view/{actual_url.platform_profile_id}").strip()
    else:
        norm_actual_url = str(actual_url).strip()

    if "#google_vignette" in norm_actual_url:
        norm_actual_url = norm_actual_url.split("#google_vignette")[0].rstrip("#")

    expected_id = expected_profile.platform_profile_id

    # 1. Check redirects to auth or deactivated pages
    lower_url = norm_actual_url.lower()
    if "/user/login" in lower_url or "/login" in lower_url or "/sign_in" in lower_url:
        err = "Session expired or redirected to login"
        if raise_on_mismatch:
            raise ProfileRedirectRejected(target_id=expected_id, redirect_url=norm_actual_url)
        return False, err

    if "/user/deactivated" in lower_url or "deactivated" in lower_url:
        err = f"Profile '{expected_id}' is deactivated on Mingle2"
        if raise_on_mismatch:
            raise ProfileRedirectRejected(target_id=expected_id, redirect_url=norm_actual_url)
        return False, err

    # 2. Extract ID from actual URL
    _, actual_id = canonicalize_profile_url(norm_actual_url)

    if expected_profile.is_paywalled_preview:
        if "who_is_interested_in_me" in lower_url:
            return True, "Paywalled fan preview verified on interested page"
        err = f"Expected paywalled preview on who_is_interested_in_me, but opened {actual_url}"
        if raise_on_mismatch:
            raise ProfileIdentityMismatch(expected_id=expected_id, actual_id=str(actual_id), reason=err)
        return False, err

    if actual_id != expected_id:
        err = f"Expected ID '{expected_id}' but actual page resolved ID '{actual_id}' (URL: {actual_url})"
        if raise_on_mismatch:
            raise ProfileIdentityMismatch(expected_id=expected_id, actual_id=str(actual_id), reason="ID mismatch")
        return False, err

    # 3. Check title / username consistency if present
    dom_user = loaded_username or (actual_dom_data.get("username") or actual_dom_data.get("name") if actual_dom_data else None)
    if dom_user:
        expected_name = expected_profile.display_name.strip()
        if expected_name and expected_name.lower() not in ("unknown", "mingle2 member"):
            exp_first = expected_name.split()[0].lower()
            act_first = dom_user.strip().split()[0].lower()
            if exp_first not in dom_user.lower() and act_first not in expected_name.lower():
                logger.warning(
                    f"Name advisory mismatch: Expected '{expected_name}', page displays '{dom_user}'. ID matches ({expected_id})."
                )

    return True, "Identity verified successfully"
