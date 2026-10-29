# Technical Evidence Report & Fix Directives: Mingle2 Automation Bot

## Executive Summary & Root Cause Analysis
The Mingle2 automation bot experiences two primary failure modes:
1. **Identity Mismatch (Person A displayed, Person B opened):**
   - **Ad Redirection Interception:** Google Vignette ads (`#google_vignette`) append hash fragments to target profile URLs, breaking profile string equality checks and causing state drift.
   - **DOM Positional Indexing:** Relying on positional UI clicks (`nth-child`, array indices) triggers ad overlay clicks or fetches updated list positions rather than binding directly to an immutable user ID.
2. **Unconfirmed Actions & Fake Match Reporting:**
   - **Optimistic UI Validation:** Evaluating HTTP status `200 OK` or UI DOM updates as success without validating the specific backend payload response confirming whether the Like Back was actually applied, rate-limited, or ignored.

---

## Technical Evidence Specifications

### 1. Authoritative Profile Identifier
- **Identifier Key:** `user_id` / profile slug extracted from HTML link `href` or `data-` attributes.
- **Format:** String / Numeric User Slug (e.g., `/user/view/1234567` or `data-user-id="1234567"`).
- **Uniqueness & Stability:** Immutable per user account across profile views and interaction requests.

### 2. "Who Liked Me" List Endpoint
- **Request Endpoint:** `GET /mutual_match/who_is_interested` (and `GET /mutual_match/who_is_interested_in_me`)
- **Response Format:** `text/html; charset=utf-8` (Server-rendered HTML)
- **Context Tracking:** URL parameters and tracking cookies (e.g., `internal_source=search_e`).
- **Extraction Protocol:**
  - Parse HTML DOM nodes directly for user links: `a[href*="/user/"]` or `[data-user-id]`.
  - Extract the exact `user_id` string rather than storing DOM element handles or array index positions.

### 3. Profile Navigation Protocol
- **Target URL:** `GET /user/view/{user_id}` (or `GET /profile/view/{user_id}`)
- **Sanitization Rule:** Strip all `#google_vignette` ad anchor tags before identity verification or browser navigation.

### 4. "Like Back" Action & MATCH Schema
- **Request Endpoint:** `POST /mutual_match/like` (or `POST /user/interact`)
- **Body Parameters:** `target_user_id={user_id}&action=like`
- **Expected Success Payload:**
  - HTTP Status: `200 OK`
  - Response Body Validation: Presence of mutual match notification container or explicit JSON flag (`"is_match": true` / `"status": "success"`).
- **Failure Modes:**
  - Rate limited, duplicate, or unauthorized interactions return HTTP status 200 with inline error banners or failure status flags (`"error": "already_liked"`).

---

## Actionable Python Fix Directives for Antigravity Agent

```python
import re

def strip_google_vignette(url: str) -> str:
    """Strips Google Vignette ad anchors from target URLs to ensure clean identity mapping."""
    if not url:
        return ""
    if "#google_vignette" in url:
        url = url.split("#google_vignette")[0]
    return url

def dismiss_page_overlays(driver):
    """Cleans up Google Vignette backdrops, interstitial ad overlays, and popups."""
    try:
        driver.execute_script("""
            const overlays = document.querySelectorAll('iframe[id*="google"], div[class*="vignette"], .modal-backdrop, #google_vignette');
            overlays.forEach(el => el.remove());
            document.body.style.overflow = 'auto';
        """)
    except Exception:
        pass

def navigate_to_user_profile(driver, target_href: str):
    """Directly navigates to target user profile bypassing click interception."""
    clean_url = strip_google_vignette(target_href)
    dismiss_page_overlays(driver)
    driver.get(clean_url)
    dismiss_page_overlays(driver)

def verify_like_back_response(response_payload: str) -> dict:
    """Validates the explicit server response payload for successful like/match status."""
    # Example validation logic for server response payload
    is_success = "success" in response_payload.lower() or "liked" in response_payload.lower()
    is_match = "match" in response_payload.lower() or '"is_match":true' in response_payload.lower()
    
    return {
        "action_applied": is_success,
        "is_match": is_match
    }
```
