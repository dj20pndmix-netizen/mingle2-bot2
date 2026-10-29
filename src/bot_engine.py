import asyncio
import logging
import math
import random
import re
from datetime import datetime, timezone
from typing import Any, Callable, Dict, List, Optional, Tuple, Union

from playwright.async_api import BrowserContext, Page, async_playwright

from src.config import settings
from src import database
from src.profile_identity import (
    Profile,
    canonicalize_profile_url,
    resolve_profile_from_raw_card,
    verify_profile_identity,
)
from src.search_engine import rank_and_filter_search_results
from src.like_state_machine import ActionState, VerificationStatus
from src.session_state import (
    SessionState,
    get_connection_display_status,
    is_session_authenticated,
    can_transition_session,
)

logger = logging.getLogger("mingle2.bot_engine")


async def setup_ad_blocking(context_or_page: Union[BrowserContext, Page]) -> None:
    """Blocks Google Ads and Vignette popups to keep page clean and clickable."""
    ad_domains = [
        "*googleads*",
        "*doubleclick.net*",
        "*googlesyndication*",
        "*google-analytics*",
        "*adservice.google*",
        "*adnxs*",
        "*taboola*",
        "*outbrain*",
    ]

    async def _abort_route(route):
        try:
            await route.abort()
        except Exception:
            pass

    for pattern in ad_domains:
        await context_or_page.route(pattern, _abort_route)


def strip_google_vignette(url: str) -> str:
    """Strips #google_vignette hash if present in URL."""
    if not url:
        return ""
    if "#google_vignette" in url:
        url = url.split("#google_vignette")[0]
    return url.rstrip("#")


async def dismiss_page_overlays(page: Page) -> None:
    """Safely closes Google Vignette backdrops, interstitial ad overlays, popups, and upgrade dialogs."""
    try:
        await page.evaluate(r"""() => {
            if (window.jQuery && window.jQuery.modal && window.jQuery.modal.close) {
                try { window.jQuery.modal.close(); } catch(e) {}
            }
            document.querySelectorAll(
                'iframe[id*="google"], div[class*="vignette"], #google_vignette, ins.adsbygoogle, div[id*="aswift"], ' +
                '.simplemodal-overlay, .simplemodal-container, #osx-overlay, ' +
                '#mingleplus-benefits-modal-container, .modal-backdrop, ' +
                '#onetrust-banner-sdk, #onetrust-consent-sdk, .remodal-wrapper'
            ).forEach(el => el.remove());

            try {
                document.body.style.overflow = 'auto';
            } catch(e) {}

            const remind = Array.from(document.querySelectorAll('a, button, div')).find(
                el => (el.innerText || '').trim() === 'Remind me later'
            );
            if (remind) remind.click();
        }""")
    except Exception:
        pass


async def navigate_to_user_profile(page: Page, target_href: str) -> None:
    """Directly navigates to target user profile bypassing click interception and ad redirects."""
    clean_url = strip_google_vignette(target_href)
    await dismiss_page_overlays(page)
    await page.goto(clean_url, wait_until="domcontentloaded", timeout=settings.request_timeout_seconds * 1000)
    await dismiss_page_overlays(page)


def verify_like_back_response(response_payload: str) -> Dict[str, Any]:
    """Validates the explicit server response payload for successful like/match status."""
    if not response_payload:
        return {"action_applied": False, "is_match": False, "reason": "empty_payload"}

    import json
    try:
        data = json.loads(response_payload)
        if isinstance(data, dict):
            status = str(data.get("status", "")).lower()
            action = str(data.get("action", "")).lower()
            error = str(data.get("error") or data.get("message") or "").lower()

            # Detect explicit error / refusal conditions
            if status == "error" or any(err in error for err in ["rate_limit", "already_liked", "unauthorized", "captcha"]):
                return {
                    "action_applied": False,
                    "is_match": False,
                    "reason": "server_reported_error",
                }

            is_match = bool(data.get("is_match") or data.get("mutual_match") or action == "mutual_match" or status == "match")
            is_success = status in ("success", "ok", "liked") or action in ("liked", "mutual_match") or is_match

            return {
                "action_applied": is_success,
                "is_match": is_match,
                "reason": "confirmed" if is_success else "unconfirmed",
            }
    except Exception:
        pass

    lower_payload = response_payload.lower()

    # Detect explicit error / refusal conditions
    if any(err in lower_payload for err in ["already_liked", "rate_limit", "unauthorized", '"error":', "captcha"]):
        return {
            "action_applied": False,
            "is_match": False,
            "reason": "server_reported_error",
        }

    # If it has explicit "is_match": false, respect it
    if '"is_match":false' in lower_payload or '"is_match": false' in lower_payload:
        is_match = False
    else:
        is_match = "mutual_match" in lower_payload or '"is_match":true' in lower_payload or '"is_match": true' in lower_payload

    is_success = "success" in lower_payload or "liked" in lower_payload or "mutual_match" in lower_payload

    return {
        "action_applied": is_success,
        "is_match": is_match,
        "reason": "confirmed" if is_success else "unconfirmed",
    }


class Mingle2BotEngine:
    """Playwright-based automation engine for Mingle2."""

    def __init__(self, log_callback: Optional[Callable[[str, str], Any]] = None):
        self._playwright = None
        self._context: Optional[BrowserContext] = None
        self._is_busy: bool = False
        self.is_login_mode: bool = False
        self._session_state: SessionState = SessionState.LOGIN_REQUIRED
        self._login_watcher_task: Optional[asyncio.Task] = None
        self._state_callbacks: List[Callable[[SessionState], Any]] = []
        self._log_callback = log_callback
        self._lock = asyncio.Lock()

    @property
    def session_state(self) -> SessionState:
        return self._session_state

    @property
    def session_status(self) -> str:
        """Legacy compatibility status string: LOGGED_IN or LOGGED_OUT."""
        return "LOGGED_IN" if is_session_authenticated(self._session_state) else "LOGGED_OUT"

    @session_status.setter
    def session_status(self, val: str) -> None:
        """Legacy compatibility setter."""
        if val == "LOGGED_IN":
            self._session_state = SessionState.CONNECTED
        elif val == "SESSION_EXPIRED":
            self._session_state = SessionState.SESSION_EXPIRED
        elif val == "LOGGED_OUT":
            if self._session_state not in (SessionState.OPENING_BROWSER, SessionState.WAITING_FOR_LOGIN):
                self._session_state = SessionState.LOGIN_REQUIRED

    @property
    def is_browser_launched(self) -> bool:
        """Determines if the Chromium browser process/context is currently active."""
        if self._context is None:
            return False
        try:
            _ = self._context.pages
            return True
        except Exception:
            return False

    def add_state_callback(self, cb: Callable[[SessionState], Any]) -> None:
        """Register listener for session state changes."""
        if cb not in self._state_callbacks:
            self._state_callbacks.append(cb)

    async def set_session_state(self, new_state: SessionState) -> None:
        """Sets session state, logs change, and notifies listeners."""
        old_state = self._session_state
        if old_state == new_state:
            return
        self._session_state = new_state
        logger.info(f"Session State changed: {old_state.value} -> {new_state.value}")
        await self._log("INFO", f"Mingle2 Session State: {new_state.value}")
        for cb in self._state_callbacks:
            try:
                res = cb(new_state)
                if asyncio.iscoroutine(res):
                    await res
            except Exception as e:
                logger.error(f"Error in session state callback: {e}")

    @property
    def active_context(self) -> Optional[BrowserContext]:
        return self._context

    @active_context.setter
    def active_context(self, val: Optional[BrowserContext]) -> None:
        self._context = val

    @property
    def playwright(self):
        return self._playwright

    @playwright.setter
    def playwright(self, val):
        self._playwright = val

    async def _log(self, level: str, msg: str) -> None:
        logger.info(f"[{level}] {msg}")
        if self._log_callback:
            try:
                res = self._log_callback(level, msg)
                if asyncio.iscoroutine(res):
                    await res
            except Exception as e:
                logger.error(f"Error executing log callback: {e}")

    async def _init_playwright(self) -> None:
        if not self._playwright:
            self._playwright = await async_playwright().start()

    async def _ensure_playwright_installed(self) -> None:
        """Check and install Chromium binaries if missing."""
        import sys
        try:
            await self._log("WARN", "Chromium binary missing. Automatically installing Playwright Chromium...")
            proc = await asyncio.create_subprocess_exec(
                sys.executable, "-m", "playwright", "install", "chromium",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, stderr = await proc.communicate()
            if proc.returncode == 0:
                await self._log("SUCCESS", "Playwright Chromium successfully installed.")
            else:
                await self._log("ERROR", f"Failed auto-installing Chromium: {stderr.decode()}")
        except Exception as e:
            logger.error(f"Error during auto-install: {e}")

    async def stop_active_browser(self) -> None:
        """Releases the lock on browser_data directory and cleans up Playwright."""
        if self._context:
            try:
                await self._context.close()
            except Exception as e:
                logger.debug(f"Context close error: {e}")
            self._context = None

        if self._playwright:
            try:
                await self._playwright.stop()
            except Exception as e:
                logger.debug(f"Playwright stop error: {e}")
            self._playwright = None

        # Brief pause to ensure Windows file system lock is completely released
        await asyncio.sleep(0.5)

    close_context = stop_active_browser

    async def get_context(self, headless: Optional[bool] = None) -> BrowserContext:
        """Get or initialize persistent browser context."""
        await self._init_playwright()
        target_headless = settings.headless_mode if headless is None else headless

        if self._context is not None:
            try:
                _ = self._context.pages
                return self._context
            except Exception:
                self._context = None

        settings.ensure_directories()
        args = [
            "--disable-blink-features=AutomationControlled",
            "--no-sandbox",
            "--disable-infobars",
            "--disable-dev-shm-usage",
            "--disable-features=IsolateOrigins,site-per-process",
        ]

        launch_kwargs = {
            "user_data_dir": str(settings.browser_data_dir),
            "headless": target_headless,
            "viewport": {"width": 1280, "height": 850},
            "user_agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36",
            "locale": "en-US",
            "timezone_id": "America/New_York",
            "args": args,
            "ignore_default_args": ["--enable-automation"],
        }

        try:
            self._context = await self._playwright.chromium.launch_persistent_context(**launch_kwargs)
        except Exception as launch_err:
            if "Executable doesn't exist" in str(launch_err) or "playwright install" in str(launch_err).lower():
                await self._ensure_playwright_installed()
                self._context = await self._playwright.chromium.launch_persistent_context(**launch_kwargs)
            else:
                raise launch_err

        # Block Google Ads and Vignette popups to keep page clean and clickable
        await setup_ad_blocking(self._context)

        # Inject anti-detection evasion scripts
        await self._context.add_init_script("""
            Object.defineProperty(navigator, 'webdriver', { get: () => undefined });
            window.chrome = { runtime: {} };
            Object.defineProperty(navigator, 'plugins', { get: () => [1, 2, 3, 4, 5] });
            Object.defineProperty(navigator, 'languages', { get: () => ['en-US', 'en'] });
        """)

        # Cross-platform cookie loader: Inject decrypted cookies from storage_state.json if available
        storage_state_file = settings.data_dir / "storage_state.json"
        if storage_state_file.exists():
            try:
                import json
                state_data = json.loads(storage_state_file.read_text(encoding="utf-8"))
                cookies = state_data.get("cookies", [])
                mingle_cookies = [c for c in cookies if "mingle2" in c.get("domain", "")]
                if mingle_cookies:
                    await self._context.add_cookies(mingle_cookies)
                    logger.info(f"Loaded {len(mingle_cookies)} cross-platform Mingle2 session cookies from storage_state.json")
            except Exception as cookie_err:
                logger.warning(f"Could not inject storage_state cookies: {cookie_err}")

        return self._context

    async def open_login_browser(self) -> Dict[str, Any]:
        """
        Launches a visible Chromium window for manual user login.
        1. Checks for duplicate browser instances to prevent resource contention.
        2. Sets session state: OPENING_BROWSER.
        3. Launches visible persistent browser context (headless=False).
        4. Navigates to Mingle2 login page.
        5. Sets session state: WAITING_FOR_LOGIN.
        6. Spawns background watcher wait_for_login(timeout=300) to detect authentication.
        7. Handles browser window closure and errors.
        """
        # Prevent duplicate browser launches if one is already opening/waiting
        if self._session_state in (SessionState.OPENING_BROWSER, SessionState.WAITING_FOR_LOGIN) and self.is_browser_launched:
            await self._log("WARN", "Browser already launched and waiting for login.")
            return {
                "status": "already_open",
                "message": "Browser is already open and waiting for user login.",
                "session_state": self._session_state.value
            }

        async with self._lock:
            await self.set_session_state(SessionState.OPENING_BROWSER)
            await self.stop_active_browser()
            self.is_login_mode = True

            try:
                await self._log("INFO", "Launching visible browser for manual Mingle2 login...")
                self._playwright = await async_playwright().start()

                launch_kwargs = {
                    "user_data_dir": str(settings.browser_data_dir),
                    "headless": False,  # MUST be visible so the user can interact
                    "viewport": {"width": 1280, "height": 850},
                    "args": [
                        "--start-maximized",
                        "--disable-blink-features=AutomationControlled",
                        "--no-sandbox",
                        "--disable-dev-shm-usage",
                    ],
                    "ignore_default_args": ["--enable-automation"],
                }

                try:
                    self._context = await self._playwright.chromium.launch_persistent_context(**launch_kwargs)
                except Exception as launch_err:
                    if "Executable doesn't exist" in str(launch_err) or "playwright install" in str(launch_err).lower():
                        await self._ensure_playwright_installed()
                        self._context = await self._playwright.chromium.launch_persistent_context(**launch_kwargs)
                    else:
                        raise launch_err

                # Block ad network trackers and overlays
                await setup_ad_blocking(self._context)

                # Stealth evasion script
                await self._context.add_init_script("""
                    Object.defineProperty(navigator, 'webdriver', { get: () => undefined });
                    window.chrome = { runtime: {} };
                    Object.defineProperty(navigator, 'plugins', { get: () => [1, 2, 3, 4, 5] });
                    Object.defineProperty(navigator, 'languages', { get: () => ['en-US', 'en'] });
                """)

                page = self._context.pages[0] if self._context.pages else await self._context.new_page()
                target_login_url = strip_google_vignette(settings.login_url)
                await page.goto(target_login_url, wait_until="domcontentloaded", timeout=45000)
                await self._log("SUCCESS", f"Browser opened successfully at Mingle2 login page: {target_login_url}")

                await self.set_session_state(SessionState.WAITING_FOR_LOGIN)

                # Handle window closure
                def on_window_close(p):
                    logger.info("Login browser page closed by user.")
                    if self._session_state in (SessionState.OPENING_BROWSER, SessionState.WAITING_FOR_LOGIN):
                        try:
                            loop = asyncio.get_event_loop()
                            if loop.is_running():
                                loop.create_task(self.set_session_state(SessionState.LOGIN_REQUIRED))
                        except Exception:
                            pass

                page.on("close", on_window_close)
                if self._context:
                    self._context.on("close", lambda ctx: setattr(self, "is_login_mode", False))

                # Cancel previous watcher if exists
                if self._login_watcher_task and not self._login_watcher_task.done():
                    self._login_watcher_task.cancel()

                # Start background task to detect when user finishes logging in
                self._login_watcher_task = asyncio.create_task(self.wait_for_login(timeout=300))

                return {
                    "status": "success",
                    "session_state": self._session_state.value,
                    "message": "Browser opened for login. Please sign in to your Mingle2 account in the opened window."
                }
            except Exception as e:
                await self._log("ERROR", f"Failed to launch login browser: {e}")
                self.is_login_mode = False
                await self.set_session_state(SessionState.ERROR)
                await self.stop_active_browser()
                raise e

    open_login_window = open_login_browser

    async def login_with_credentials(self, login: str, password: str) -> Dict[str, Any]:
        """
        Authenticates directly to Mingle2 using login credentials in headless mode.
        Designed for Cloud/Render environments where physical GUI cannot be shown.
        """
        if not login or not password:
            return {"status": "error", "message": "Email/Username and password are required."}

        async with self._lock:
            await self._log("INFO", f"Attempting direct headless login for '{login}'...")
            await self.set_session_state(SessionState.OPENING_BROWSER)

            try:
                context = await self.get_context(headless=True)
                page = await context.new_page()
                await setup_ad_blocking(page)

                await page.goto("https://mingle2.com/user/login", wait_until="domcontentloaded", timeout=45000)
                await dismiss_page_overlays(page)
                await asyncio.sleep(1.5)

                # Check if already authenticated
                cur_url = strip_google_vignette(page.url.lower())
                if "/user/login" not in cur_url and "/sign_in" not in cur_url and "step1" not in cur_url:
                    await page.close()
                    await self.set_session_state(SessionState.CONNECTED)
                    return {"status": "success", "message": "Already authenticated on Mingle2!"}

                # Find input fields
                login_input = page.locator("input[name='login'], input#login").first
                pwd_input = page.locator("input[name='password'], input#password").first

                if await login_input.count() == 0 or await pwd_input.count() == 0:
                    await page.close()
                    await self.set_session_state(SessionState.LOGIN_REQUIRED)
                    return {"status": "error", "message": "Could not locate Mingle2 login form fields."}

                await login_input.fill(login)
                await asyncio.sleep(0.4)
                await pwd_input.fill(password)
                await asyncio.sleep(0.4)

                submit_btn = page.locator("button[type='submit'], input[type='submit'], .form-with-recaptcha-action").first
                if await submit_btn.count() > 0:
                    await submit_btn.click()
                else:
                    await page.keyboard.press("Enter")

                # Wait for form submission and redirect
                await asyncio.sleep(4.5)
                await dismiss_page_overlays(page)

                # Check for explicit error banners or alerts on page
                error_el = page.locator(
                    ".alert, .alert-warning, .alert-danger, .error, .flash_error, .notice-error, .invalid-feedback, "
                    "div:text-matches('incorrect|invalid|wrong|error|captcha', 'i')"
                ).first
                if await error_el.count() > 0:
                    err_text = await error_el.inner_text()
                    clean_err = err_text.strip().replace("\n", " ")
                    if clean_err:
                        await page.close()
                        await self.set_session_state(SessionState.LOGIN_REQUIRED)
                        return {"status": "error", "message": f"Mingle2: {clean_err}"}

                # Check if current URL transitioned to an authenticated section
                after_url = strip_google_vignette(page.url.lower())
                if "/user/login" not in after_url and "/verify_login" not in after_url and "/sign_in" not in after_url and "step1" not in after_url:
                    storage_path = settings.data_dir / "storage_state.json"
                    try:
                        await context.storage_state(path=str(storage_path))
                    except Exception:
                        pass

                    await page.close()
                    await self.set_session_state(SessionState.CONNECTED)
                    await self._log("SUCCESS", f"Direct login verified successfully for '{login}'!")

                    try:
                        from src.session_manager import export_session_archive
                        export_session_archive()
                    except Exception:
                        pass

                    try:
                        await self.sync_account_profile()
                    except Exception:
                        pass

                    return {"status": "success", "message": "Successfully logged into Mingle2!"}

                # Navigate to mutual match to confirm active authenticated session
                try:
                    await page.goto("https://mingle2.com/mutual_match/who_is_interested_in_me", wait_until="domcontentloaded", timeout=25000)
                    test_url = strip_google_vignette(page.url.lower())
                    if "/user/login" not in test_url and "/verify_login" not in test_url and "/sign_in" not in test_url and "step1" not in test_url:
                        storage_path = settings.data_dir / "storage_state.json"
                        try:
                            await context.storage_state(path=str(storage_path))
                        except Exception:
                            pass

                        await page.close()
                        await self.set_session_state(SessionState.CONNECTED)
                        await self._log("SUCCESS", f"Direct login verified successfully for '{login}'!")

                        try:
                            from src.session_manager import export_session_archive
                            export_session_archive()
                        except Exception:
                            pass

                        try:
                            await self.sync_account_profile()
                        except Exception:
                            pass

                        return {"status": "success", "message": "Successfully logged into Mingle2!"}
                except Exception as ex:
                    logger.debug(f"Post-login verification error: {ex}")

                await page.close()
                await self.set_session_state(SessionState.LOGIN_REQUIRED)
                return {
                    "status": "error",
                    "message": "Login was rejected by Mingle2 or reCAPTCHA challenge was triggered. Please use 'Upload Session Zip' (select minglebot_session.zip from your PC) to instantly authenticate."
                }

            except Exception as e:
                await self._log("ERROR", f"Direct login error: {e}")
                await self.set_session_state(SessionState.LOGIN_REQUIRED)
                return {"status": "error", "message": str(e)}

    async def wait_for_login(self, timeout: int = 300) -> bool:
        """
        Polls the opened browser to detect authenticated Mingle2 application state.
        Observable indicators:
        - URL navigated away from login/register to authenticated page (/mutual_match, /inbox, /user/my_profile, etc.)
        - Presence of user avatar / user menu / logout link
        - Absence of #login-form
        Transitions: WAITING_FOR_LOGIN -> LOGIN_DETECTED -> SESSION_VERIFIED -> CONNECTED.
        """
        start_time = asyncio.get_event_loop().time()
        await self._log("INFO", "Waiting for Mingle2 sign-in to complete in browser...")

        while (asyncio.get_event_loop().time() - start_time) < timeout:
            if not self.is_browser_launched:
                await self._log("INFO", "Browser was closed before login completed.")
                if self._session_state in (SessionState.WAITING_FOR_LOGIN, SessionState.OPENING_BROWSER):
                    await self.set_session_state(SessionState.LOGIN_REQUIRED)
                return False

            if self._session_state not in (SessionState.WAITING_FOR_LOGIN, SessionState.OPENING_BROWSER):
                return self._session_state in (SessionState.CONNECTED, SessionState.SESSION_VERIFIED)

            try:
                pages = self._context.pages if self._context else []
                for page in pages:
                    try:
                        current_url = strip_google_vignette(page.url.lower())
                        if not current_url.startswith("http"):
                            continue

                        is_mingle = "mingle2.com" in current_url
                        is_login_page = any(x in current_url for x in ["/user/login", "/sign_in", "register", "step1"])

                        # Check if on Mingle2 and past login
                        if is_mingle and not is_login_page:
                            # Verify authentic observable DOM state
                            is_auth = await page.evaluate("""
                                () => {
                                    const logoutBtn = document.querySelector('a[href*="logout"], a[href*="sign_out"]');
                                    const userNav = document.querySelector('.user-menu, .profile-nav, .user-avatar, #nav-user, a[href*="/inbox"], a[href*="/user/my_profile"], #header-user-menu, .nav-item-user');
                                    const hasLoginForm = !!document.querySelector('#login-form, form[action*="login"]');
                                    if (hasLoginForm) return false;
                                    if (logoutBtn || userNav) return true;
                                    const matchCard = document.querySelector('.mutual-match-card, #match-actions, button.btn-like, #btn-like');
                                    return !!matchCard;
                                }
                            """)

                            if is_auth:
                                await self.set_session_state(SessionState.LOGIN_DETECTED)
                                await self._log("SUCCESS", "Mingle2 authentication detected in browser!")
                                await asyncio.sleep(0.5)

                                await self.set_session_state(SessionState.SESSION_VERIFIED)
                                await asyncio.sleep(0.5)

                                # Save persistent storage_state.json
                                try:
                                    storage_file = settings.data_dir / "storage_state.json"
                                    await self._context.storage_state(path=str(storage_file))
                                except Exception as ss_err:
                                    logger.debug(f"Could not persist storage_state: {ss_err}")

                                # Sync account profile
                                try:
                                    await self.sync_account_profile()
                                except Exception as sync_err:
                                    logger.warning(f"Profile sync notice: {sync_err}")

                                await self.set_session_state(SessionState.CONNECTED)
                                await self._log("SUCCESS", "Mingle2 session verified: CONNECTED.")
                                self.is_login_mode = False
                                return True
                    except Exception as p_err:
                        logger.debug(f"Page evaluation check: {p_err}")
            except Exception as loop_err:
                logger.debug(f"Wait for login check: {loop_err}")

            await asyncio.sleep(2)

        await self._log("WARN", "Login detection timed out. Please try again.")
        if self._session_state == SessionState.WAITING_FOR_LOGIN:
            await self.set_session_state(SessionState.LOGIN_REQUIRED)
        return False

    async def verify_mingle2_session(self) -> bool:
        """
        Determines whether the currently connected browser is still authenticated.
        Every automation operation verifies session before performing work.
        If verification fails:
        - calls handle_session_expiration()
        - stops queued actions
        - marks session as SESSION_EXPIRED
        - notifies UI
        """
        try:
            context = await self.get_context()
            page = await context.new_page()
            try:
                target_url = "https://mingle2.com/mutual_match/match"
                await page.goto(
                    target_url,
                    wait_until="domcontentloaded",
                    timeout=30000,
                )
                await dismiss_page_overlays(page)
                current_url = strip_google_vignette(page.url.lower())

                if any(x in current_url for x in ["/user/login", "/login", "/sign_in", "step1", "register"]):
                    await page.close()
                    await self.handle_session_expiration("Redirected to login page.")
                    return False

                is_authenticated = await page.evaluate("""
                    () => {
                        const logoutBtn = document.querySelector('a[href*="logout"], a[href*="sign_out"]');
                        const userNav = document.querySelector('.user-menu, .profile-nav, .user-avatar, #nav-user, a[href*="/inbox"], a[href*="/user/my_profile"], #header-user-menu, .nav-item-user');
                        const hasLoginForm = !!document.querySelector('#login-form, form[action*="login"]');
                        if (hasLoginForm) return false;
                        if (logoutBtn || userNav) return true;
                        const matchCard = document.querySelector('.mutual-match-card, #match-actions, button.btn-like, #btn-like');
                        return !!matchCard;
                    }
                """)

                # Refresh storage state if verified
                if is_authenticated:
                    try:
                        storage_file = settings.data_dir / "storage_state.json"
                        await context.storage_state(path=str(storage_file))
                    except Exception:
                        pass
                    await page.close()
                    if self._session_state != SessionState.CONNECTED:
                        await self.set_session_state(SessionState.CONNECTED)
                    return True
                else:
                    await page.close()
                    await self.handle_session_expiration("Authenticated DOM indicators missing.")
                    return False
            except Exception as page_err:
                try:
                    await page.close()
                except Exception:
                    pass
                await self.handle_session_expiration(f"Navigation error: {page_err}")
                return False
        except Exception as e:
            await self.handle_session_expiration(f"Browser error: {e}")
            return False

    verify_session = verify_mingle2_session
    check_login_status = verify_mingle2_session

    async def handle_session_expiration(self, reason: str = "") -> None:
        """
        Transitions state to SESSION_EXPIRED.
        Logs warning and triggers session expiration handling.
        """
        if self._session_state == SessionState.SESSION_EXPIRED:
            return
        await self.set_session_state(SessionState.SESSION_EXPIRED)
        msg = f"SESSION EXPIRED — LOGIN REQUIRED. {reason}".strip()
        await self._log("WARN", msg)

    async def sync_account_profile(self) -> Dict[str, Any]:
        """
        Extracts user profile details from the authenticated session (or header DOM)
        and persists them via database.save_account_profile.
        Ensures NO passwords, cookies, or authorization tokens are stored.
        """
        profile_data: Dict[str, Any] = {
            "user_id": "my_account",
            "username": "Mingle2 User",
            "headline": "",
            "about_me": "",
            "age": None,
            "location": "",
            "avatar_url": "",
            "photos": [],
            "stats": {},
        }
        try:
            if self._context and self._context.pages:
                page = self._context.pages[0]
                extracted = await page.evaluate(r"""
                    () => {
                        const data = {};
                        const userLink = document.querySelector('a[href*="/user/my_profile"], a[href*="/users/view/"], .user-menu .username, .user-avatar a');
                        if (userLink) {
                            const href = userLink.getAttribute('href') || '';
                            const match = href.match(/\/users?\/view\/([^\/?#]+)/i);
                            if (match) data.user_id = match[1];
                            const text = userLink.innerText.trim();
                            if (text) data.username = text;
                        }
                        const nameEl = document.querySelector('.user-menu .display-name, #header-user-menu .username, .nav-item-user .name');
                        if (nameEl && nameEl.innerText.trim()) {
                            data.username = nameEl.innerText.trim();
                        }
                        const avatarImg = document.querySelector('.user-avatar img, img.user_thumbnail, #header-user-menu img');
                        if (avatarImg) {
                            data.avatar_url = avatarImg.getAttribute('src') || avatarImg.getAttribute('data-src') || '';
                        }
                        return data;
                    }
                """)
                if extracted and isinstance(extracted, dict):
                    if extracted.get("user_id"):
                        profile_data["user_id"] = extracted["user_id"]
                    if extracted.get("username"):
                        profile_data["username"] = extracted["username"]
                    if extracted.get("avatar_url"):
                        profile_data["avatar_url"] = extracted["avatar_url"]
        except Exception as e:
            logger.debug(f"sync_account_profile extraction note: {e}")

        # Persist to database
        try:
            saved = await database.save_account_profile(profile_data)
            return saved or profile_data
        except Exception as e:
            logger.warning(f"Failed to persist account profile: {e}")
            return profile_data

    async def get_current_account_state(self) -> Dict[str, Any]:
        """
        Returns connected account state without passwords, cookies, or auth tokens.
        """
        account = await database.get_account_profile()
        return {
            "session_state": self._session_state.value,
            "connection_status": get_connection_display_status(self._session_state),
            "is_authenticated": is_session_authenticated(self._session_state),
            "session_verified": self._session_state in (SessionState.SESSION_VERIFIED, SessionState.CONNECTED),
            "browser_launched": self.is_browser_launched,
            "account": {
                "user_id": account.get("user_id") if account else None,
                "username": account.get("username") if account else "Not connected",
                "headline": account.get("headline") if account else "",
                "location": account.get("location") if account else "",
                "age": account.get("age") if account else None,
                "avatar_url": account.get("avatar_url") if account else None,
                "synced_at": account.get("synced_at") if account else None,
            } if account else None,
        }

    async def close_browser(self) -> None:
        """Closes browser context and resets login mode."""
        if self._login_watcher_task and not self._login_watcher_task.done():
            self._login_watcher_task.cancel()
            self._login_watcher_task = None
        await self.stop_active_browser()
        self.is_login_mode = False
        if self._session_state in (SessionState.OPENING_BROWSER, SessionState.WAITING_FOR_LOGIN):
            await self.set_session_state(SessionState.LOGIN_REQUIRED)

    async def reconnect_browser(self) -> bool:
        """Closes active context and attempts clean re-connection and session verification."""
        await self.close_browser()
        return await self.verify_mingle2_session()

    async def scan_interested_users(self, max_pages: int = 10) -> List[Dict[str, Any]]:
        """
        Navigate to https://mingle2.com/mutual_match/who_is_interested_in_me
        and scrape list of likers across all paginated pages.
        Supports Mingle2's .user-gallery-item.fan-item cards, blurred cards, and standard cards.
        """
        if settings.test_mode:
            await self._log("INFO", "[TEST_MODE] Skipping live network scan for interested users.")
            return []

        async with self._lock:
            await self._log("INFO", "Scanning Mingle2 for incoming likes across all pages...")
            context = await self.get_context()
            page = await context.new_page()

            detected_users: List[Dict[str, Any]] = []
            seen_ids = set()

            try:
                page_num = 1
                while page_num <= max_pages:
                    target_url = f"https://mingle2.com/mutual_match/who_is_interested_in_me?page={page_num}"
                    target_url = strip_google_vignette(target_url)

                    try:
                        await page.goto(
                            target_url,
                            wait_until="domcontentloaded",
                            timeout=45000,
                        )
                    except Exception as goto_err:
                        await self._log("WARN", f"Page {page_num} load timed out ({goto_err}), checking if DOM elements are available...")
                        await asyncio.sleep(2.0)

                    current_url = strip_google_vignette(page.url.lower())

                    if "/user/login" in current_url or any(x in current_url for x in ["login", "sign_in", "step1", "nexturl", "register"]):
                        self.session_status = "LOGGED_OUT"
                        await self._log("WARN", "Not authenticated on Mingle2! Redirected to /user/login. Please click 'Open Browser for Login' to sign in.")
                        await page.close()
                        return []

                    self.session_status = "LOGGED_IN"
                    await asyncio.sleep(1.8)

                    # Extract cards from current page
                    page_cards = await page.evaluate(r"""
                        () => {
                            const results = [];

                            // 1. Mingle2 Fan Items (.user-gallery-item.fan-item)
                            const fanItems = document.querySelectorAll('.user-gallery-item.fan-item');
                            if (fanItems.length > 0) {
                                fanItems.forEach(el => {
                                    const ratingId = el.getAttribute('data-rating-id') || '';
                                    const buttonId = el.getAttribute('data-button-id') || '';
                                    const isBlur = el.classList.contains('blur-photo') || !!el.querySelector('a.payment-btn, a[href*="payment"]');
                                    const userLink = el.querySelector('a[href*="/user/view/"], a[href*="/users/view/"], a[href*="/profile/"]');

                                    const nameEl = el.querySelector('.display-name, .username, strong');
                                    const username = nameEl ? nameEl.innerText.trim() : `Interested Member`;

                                    const img = el.querySelector('img.user_thumbnail, img');
                                    const avatar = img ? (img.getAttribute('data-src') || img.src) : null;

                                    const genderAgeEl = el.querySelector('.gender-and-age');
                                    const locEl = el.querySelector('.location');
                                    const dateEl = el.querySelector('.date');

                                    const parts = [];
                                    if (genderAgeEl && genderAgeEl.innerText.trim()) parts.push(genderAgeEl.innerText.trim());
                                    if (locEl && locEl.innerText.trim()) parts.push(locEl.innerText.trim());
                                    if (dateEl && dateEl.innerText.trim()) parts.push(dateEl.innerText.trim());
                                    const ageLocation = parts.join(' • ');

                                    // Extract real user ID if unblurred/unlocked
                                    let realUserId = null;
                                    let verifiedUrl = null;
                                    if (userLink && userLink.href && !userLink.href.includes('javascript:')) {
                                        const m = userLink.href.match(/(?:users\/view|user\/view|profile)\/([a-zA-Z0-9_-]+)/i);
                                        if (m) {
                                            realUserId = m[1];
                                            verifiedUrl = `https://mingle2.com/users/view/${realUserId}`;
                                        }
                                    }

                                    // If paywalled / blurred: DO NOT use ratingId as user profile ID!
                                    if (!realUserId && (isBlur || !userLink)) {
                                        const pseudoId = `fan_preview_${ratingId || buttonId || Math.random().toString(36).substring(2, 8)}`;
                                        results.push({
                                            user_id: pseudoId,
                                            username: username,
                                            profile_url: 'https://mingle2.com/mutual_match/who_is_interested_in_me',
                                            avatar_img_url: avatar,
                                            age_location: ageLocation,
                                            is_already_liked: false,
                                            is_fan_preview: true,
                                            data_rating_id: ratingId,
                                            data_button_id: buttonId
                                        });
                                        return;
                                    }

                                    const finalUid = realUserId || ratingId || buttonId;
                                    results.push({
                                        user_id: finalUid,
                                        username: username,
                                        profile_url: verifiedUrl || `https://mingle2.com/users/view/${finalUid}`,
                                        avatar_img_url: avatar,
                                        age_location: ageLocation,
                                        is_already_liked: false,
                                        is_fan_preview: false,
                                        data_rating_id: ratingId,
                                        data_button_id: buttonId
                                    });
                                });
                                return results;
                            }

                            // 2. Standard user match containers
                            const candidateCards = document.querySelectorAll(
                                '.match_card, .user_card, .interested_user, .user-item, .profile-item, ' +
                                'div[id^="user_"], div[data-user-id], .card, li.user'
                            );

                            function parseCard(el) {
                                const link = el.querySelector('a[href*="/users/view/"], a[href*="/user/view/"], a[href*="/profile/"], a[href*="/inbox/mail_detail/"]');
                                if (!link) return null;

                                const href = link.href;
                                const match = href.match(/(?:users\/view|user\/view|profile|mail_detail|user)\/([a-zA-Z0-9_-]+)/i);
                                const userId = el.getAttribute('data-user-id') || (match ? match[1] : null);
                                if (!userId) return null;

                                const nameEl = el.querySelector('.username, .name, h2, h3, h4, strong') || link;
                                const username = (nameEl ? nameEl.innerText : 'Unknown').trim();

                                const img = el.querySelector('img');
                                const avatar = img ? (img.getAttribute('data-src') || img.src) : null;

                                const detailsEl = el.querySelector('.user_details, .details, .location, .meta, p');
                                const ageLocation = detailsEl ? detailsEl.innerText.replace(/\s+/g, ' ').trim() : '';

                                const likeBtn = el.querySelector('button, a.like, .btn-like, [data-action="like"], .like-btn');
                                const isAlreadyLiked = likeBtn ? (
                                    likeBtn.classList.contains('liked') ||
                                    likeBtn.innerText.toLowerCase().includes('liked') ||
                                    likeBtn.getAttribute('aria-pressed') === 'true'
                                ) : false;

                                return {
                                    user_id: userId,
                                    username: username || `User ${userId}`,
                                    profile_url: `https://mingle2.com/users/view/${userId}`,
                                    avatar_img_url: avatar,
                                    age_location: ageLocation,
                                    is_already_liked: isAlreadyLiked,
                                    is_fan_preview: false
                                };
                            }

                            if (candidateCards.length > 0) {
                                candidateCards.forEach(c => {
                                    const parsed = parseCard(c);
                                    if (parsed && parsed.user_id) {
                                        results.push(parsed);
                                    }
                                });
                            }

                            // 3. Fallback: direct links
                            if (results.length === 0) {
                                const links = document.querySelectorAll('a[href*="/users/view/"], a[href*="/user/view/"], a[href*="/inbox/mail_detail/"], a[href*="/profile/"]');
                                links.forEach(l => {
                                    const href = l.href;
                                    const m = href.match(/(?:users\/view|user\/view|profile|mail_detail|user)\/([a-zA-Z0-9_-]+)/i);
                                    if (!m) return;
                                    const uId = m[1];

                                    const container = l.closest('div') || l.parentElement;
                                    const img = container ? container.querySelector('img') : null;
                                    const avatar = img ? (img.getAttribute('data-src') || img.src) : null;
                                    const username = l.innerText.trim() || `User ${uId}`;

                                    results.push({
                                        user_id: uId,
                                        username: username,
                                        profile_url: `https://mingle2.com/users/view/${uId}`,
                                        avatar_img_url: avatar,
                                        age_location: '',
                                        is_already_liked: false,
                                        is_fan_preview: false
                                    });
                                });
                            }

                            return results;
                        }
                    """)

                    if not page_cards:
                        break

                    added_on_page = 0
                    now = datetime.now(timezone.utc)
                    for u in page_cards:
                        uid = str(u.get("user_id"))
                        if uid in seen_ids:
                            continue
                        seen_ids.add(uid)
                        added_on_page += 1

                        # Resolve into normalized profile and upsert into profiles database
                        profile_entity = resolve_profile_from_raw_card(u, source="FAN_PREVIEW" if u.get("is_fan_preview") else "MUTUAL_MATCH")
                        await database.upsert_profile(profile_entity)

                        user_data = {
                            "user_id": profile_entity.platform_profile_id,
                            "username": profile_entity.display_name,
                            "profile_url": profile_entity.profile_url,
                            "avatar_img_url": profile_entity.photo_reference,
                            "age_location": profile_entity.location,
                            "is_already_liked": bool(u.get("is_already_liked", False)),
                            "is_fan_preview": profile_entity.is_paywalled_preview,
                            "detected_at": now,
                        }
                        detected_users.append(user_data)

                    await self._log("INFO", f"Page {page_num}: Found {len(page_cards)} liker card(s) ({added_on_page} new).")

                    # Check if next page exists in pagination
                    has_next = await page.evaluate(r"""
                        () => {
                            const nextLink = document.querySelector('.pagination .next_page:not(.disabled), a[rel="next"], a.next');
                            return !!nextLink;
                        }
                    """)

                    if not has_next:
                        break

                    page_num += 1

                await page.close()
                await self._log("SUCCESS", f"Scan complete across {page_num} page(s). Total real interested likers: {len(detected_users)}.")
                return detected_users

            except Exception as e:
                await self._log("ERROR", f"Error during scrape scan: {e}")
                try:
                    await page.close()
                except Exception:
                    pass
                return []

    async def _human_mouse_move(self, page: Page, target_x: float, target_y: float) -> None:
        """Simulate realistic human-like curved mouse movement with jitter."""
        start_x = random.randint(100, 300)
        start_y = random.randint(100, 300)
        steps = random.randint(15, 25)

        for i in range(steps):
            t = (i + 1) / steps
            # Smooth ease-in-out curve
            ease = 0.5 * (1 - math.cos(math.pi * t))
            curr_x = start_x + (target_x - start_x) * ease + random.uniform(-2, 2)
            curr_y = start_y + (target_y - start_y) * ease + random.uniform(-2, 2)
            await page.mouse.move(curr_x, curr_y)
            await asyncio.sleep(random.uniform(0.01, 0.025))

    async def get_badge_count(self, page: Optional[Page] = None) -> Optional[str]:
        """Read the current incoming likes count badge from navbar or page text."""
        should_close = False
        if not page:
            context = await self.get_context()
            page = await context.new_page()
            should_close = True
            try:
                await page.goto("https://mingle2.com/mutual_match/match", wait_until="commit", timeout=20000)
                await asyncio.sleep(1.0)
            except Exception:
                pass

        try:
            badge = await page.evaluate(r"""() => {
                const el = document.querySelector('.annotation, .dropbtn .annotation, a[href*="mutual_match"] .badge');
                if (el && el.innerText.trim()) return el.innerText.trim();
                const m = document.body.innerText.match(/Who Likes Me \((\d+) New\)/i);
                if (m) return m[1];
                return null;
            }""")
            return badge
        except Exception:
            return None
        finally:
            if should_close and page:
                try:
                    await page.close()
                except Exception:
                    pass

    async def auto_like_mutual_matches(self, limit: int = 20, task_id: Optional[int] = None) -> Dict[str, Any]:
        """
        Autonomously rates and likes back candidates on https://mingle2.com/mutual_match/match.
        This is Mingle2's direct engine for mutual matches, matching incoming likers and decrementing
        the 'Who Likes Me' / 'Match' counter in real time.
        """
        async with self._lock:
            await self._log("INFO", f"Starting MutualMatch auto-like engine (target: {limit} likes)...")
            context = await self.get_context()
            page = await context.new_page()

            liked_users: List[Dict[str, Any]] = []
            try:
                target_url = "https://mingle2.com/mutual_match/match"
                await page.goto(target_url, wait_until="commit", timeout=25000)
                await asyncio.sleep(1.8)

                current_url = strip_google_vignette(page.url.lower())
                if "/user/login" in current_url or any(x in current_url for x in ["login", "sign_in", "step1", "nexturl"]):
                    self.session_status = "LOGGED_OUT"
                    await self._log("WARN", "Not authenticated on Mingle2! Redirected to login. Please log in first.")
                    await page.close()
                    return {"success": False, "count": 0, "reason": "Not authenticated"}

                self.session_status = "LOGGED_IN"
                await dismiss_page_overlays(page)

                initial_badge = await self.get_badge_count(page)
                await self._log("INFO", f"Initial incoming likes badge count: {initial_badge}")

                for idx in range(limit):
                    # Check task cancellation
                    if task_id:
                        current_task = await database.get_task_by_id(task_id)
                        if current_task and current_task.get("status") == "CANCELLED":
                            await self._log("WARN", f"[TASK #{task_id}] Cancelled by user.")
                            break

                    await dismiss_page_overlays(page)

                    # Inspect current candidate card
                    candidate = await page.evaluate(r"""() => {
                        const nameEl = document.querySelector('h1, h2, h3, .name, .username');
                        const form = document.querySelector('.rate-yes-btn form');
                        const img = document.querySelector('.mutual-card img, .thumbnail-frame img, .user_thumbnail, img');
                        const ageLoc = document.querySelector('.gender-and-age, .age-location, .location, .meta, p');
                        return {
                            name: nameEl ? nameEl.innerText.trim() : 'Mingle2 Member',
                            formAction: form ? form.action : null,
                            avatar: img ? img.src : null,
                            details: ageLoc ? ageLoc.innerText.trim() : ''
                        };
                    }""")

                    name = candidate.get("name", "User")
                    action = candidate.get("formAction")
                    if not action:
                        await self._log("WARN", f"Candidate like form not found on step {idx+1}. Reloading match page...")
                        await page.goto(target_url, wait_until="commit", timeout=25000)
                        await asyncio.sleep(2.0)
                        await dismiss_page_overlays(page)
                        continue

                    # Extract real user ID from action /mutual_match/rate/{userId}
                    m = re.search(r"/rate/(\d+)", action)
                    real_user_id = m.group(1) if m else f"mm_{idx}_{int(datetime.now().timestamp())}"

                    # Update task progress if task_id provided
                    if task_id:
                        pct = 15 + int(((idx + 1) / limit) * 80)
                        await database.update_task_progress(
                            task_id=task_id,
                            progress=pct,
                            current_step=f"Liking back candidate {idx+1}/{limit}: '{name}'..."
                        )

                    # Perform like rating via direct POST or form submission
                    rate_success = await page.evaluate(r"""async () => {
                        try {
                            const form = document.querySelector('.rate-yes-btn form');
                            if (!form) return { success: false, reason: 'no form' };
                            const tokenInput = form.querySelector('input[name="authenticity_token"]');
                            const token = tokenInput ? tokenInput.value : '';
                            const formData = new FormData();
                            formData.append('utf8', '✓');
                            formData.append('authenticity_token', token);
                            formData.append('rating', 'Y');

                            const resp = await fetch(form.action, {
                                method: 'POST',
                                body: formData,
                                headers: { 'X-Requested-With': 'XMLHttpRequest' }
                            });
                            return { success: resp.status === 200 };
                        } catch(e) {
                            return { success: false, error: e.toString() };
                        }
                    }""")

                    if rate_success.get("success"):
                        now = datetime.now(timezone.utc)
                        liked_users.append({
                            "user_id": real_user_id,
                            "username": name,
                            "profile_url": f"https://mingle2.com/user/view/{real_user_id}",
                            "avatar_img_url": candidate.get("avatar"),
                            "age_location": candidate.get("details", ""),
                            "liked_at": now.isoformat(),
                        })

                        # Record in database as LIKED
                        await database.add_or_get_liker(
                            user_id=real_user_id,
                            username=name,
                            profile_url=f"https://mingle2.com/user/view/{real_user_id}",
                            avatar_img_url=candidate.get("avatar"),
                            age_location=candidate.get("details", ""),
                            detected_at=now,
                            scheduled_like_at=now,
                        )
                        await database.update_status(real_user_id, "LIKED")

                        # Log success
                        await self._log("SUCCESS", f"Liked back '{name}' (ID: {real_user_id}) on MutualMatch.")

                        # Reload/advance to next candidate smoothly
                        await page.goto(target_url, wait_until="commit", timeout=25000)
                        await asyncio.sleep(random.uniform(1.8, 3.2))
                        await dismiss_page_overlays(page)

                        curr_badge = await self.get_badge_count(page)
                        if curr_badge and curr_badge != initial_badge:
                            await self._log("SUCCESS", f"Likes count reduced to: {curr_badge}!")
                    else:
                        # Fallback: submit form via DOM click
                        await dismiss_page_overlays(page)
                        btn = page.locator('.rate-yes-btn button[type="submit"], .rate-yes-btn').first
                        if await btn.count() > 0:
                            await btn.click(force=True)
                            await asyncio.sleep(2.5)
                            await self._log("SUCCESS", f"Clicked Like button for '{name}'.")

                final_badge = await self.get_badge_count(page)
                await page.close()
                await self._log("SUCCESS", f"MutualMatch auto-like complete! Liked {len(liked_users)} user(s). Current badge: {final_badge}.")
                return {"success": True, "count": len(liked_users), "users": liked_users, "final_badge": final_badge}

            except Exception as e:
                await self._log("ERROR", f"Error in auto_like_mutual_matches: {e}")
                try:
                    await page.close()
                except Exception:
                    pass
                return {"success": False, "count": len(liked_users), "error": str(e)}

    async def verify_profile_on_platform(self, profile_id: str) -> Tuple[bool, str, Optional[Dict[str, Any]]]:
        """
        Verify that a profile identifier legitimately exists on Mingle2 and resolves without
        redirects to deactivated, login, or identity mismatch.
        """
        clean_id = str(profile_id).strip()
        if not clean_id or clean_id.startswith("fan_preview_") or clean_id.startswith("fan_"):
            return False, "Paywalled fan preview does not expose direct platform member ID", None

        if clean_id.startswith("sim_"):
            return True, "Identity verified (Simulation)", {
                "platform_profile_id": clean_id,
                "profile_url": f"https://mingle2.com/users/view/{clean_id}",
                "username": f"Simulated Member {clean_id}",
                "details": "Simulated profile",
            }

        async with self._lock:
            context = await self.get_context()
            page = await context.new_page()
            try:
                target_url = f"https://mingle2.com/users/view/{clean_id}"
                await page.goto(target_url, wait_until="domcontentloaded", timeout=settings.request_timeout_seconds * 1000)
                await asyncio.sleep(1.5)
                await dismiss_page_overlays(page)

                current_url = strip_google_vignette(page.url.lower())
                if "/user/login" in current_url or any(x in current_url for x in ["login", "sign_in"]):
                    self.session_status = "LOGGED_OUT"
                    await page.close()
                    return False, "Session expired or login required", None

                if "/user/deactivated" in current_url or "deactivated" in current_url:
                    await page.close()
                    return False, f"Profile '{clean_id}' is deactivated on Mingle2", None

                # Extract ID from loaded page
                _, actual_id = canonicalize_profile_url(current_url)
                if actual_id and str(actual_id) != str(clean_id):
                    await page.close()
                    return False, f"PROFILE IDENTITY MISMATCH: Expected ID '{clean_id}', page resolved '{actual_id}' (URL: {current_url})", None

                # Extract verified profile information
                info = await page.evaluate(r"""() => {
                    const nameEl = document.querySelector('h1, h2, .profile-name, .username, .name');
                    const detailsEl = document.querySelector('.gender-and-age, .age-location, .location, .details, .user-details');
                    const headlineEl = document.querySelector('.headline, .profile-headline, h2.headline');
                    const aboutMeEl = document.querySelector('.about-me, #about_me, .profile-bio, .user-description');
                    const img = document.querySelector('.profile-avatar img, .avatar img, #profile_pic, img.user-pic, img[src*="/photos/"], img[src*="/users/"]');
                    return {
                        username: nameEl ? nameEl.innerText.trim() : 'Mingle2 Member',
                        details: detailsEl ? detailsEl.innerText.trim() : '',
                        headline: headlineEl ? headlineEl.innerText.trim() : '',
                        about_me: aboutMeEl ? aboutMeEl.innerText.trim() : '',
                        avatar_url: img ? (img.getAttribute('data-src') || img.src) : ''
                    };
                }""")
                await page.close()

                verified_data = {
                    "platform_profile_id": clean_id,
                    "profile_url": target_url,
                    "username": info.get("username", "Mingle2 Member"),
                    "details": info.get("details", ""),
                    "headline": info.get("headline", ""),
                    "about_me": info.get("about_me", ""),
                    "avatar_url": info.get("avatar_url", ""),
                }
                return True, "Identity verified successfully", verified_data

            except Exception as e:
                try:
                    await page.close()
                except Exception:
                    pass
                return False, f"Verification failed: {e}", None

    async def like_user(self, user_info: Dict[str, Any], is_test_mode: Optional[bool] = None) -> Tuple[bool, str, str]:
        """
        Execute automated 'Like' / 'Match' action on Mingle2 for specified user.
        Strictly enforces:
        1. Test Mode check (zero external platform mutations)
        2. Paywalled fan preview check (rejects fake rating IDs)
        3. Session authentication check
        4. Candidate Identity Verification on https://mingle2.com/mutual_match/match/{user_id}
        5. Execution & Verification step
        Returns: (success: bool, message: str, status: str)
        """
        user_id = str(user_info.get("user_id") or user_info.get("platform_profile_id", "")).strip()
        username = str(user_info.get("username") or user_info.get("display_name", "user")).strip()
        test_mode_active = settings.test_mode if is_test_mode is None else is_test_mode

        # 1. Handle Test Mode / Simulated Likers cleanly without external network mutations
        if test_mode_active or user_id.startswith("sim_"):
            await self._log("INFO", f"[TEST_MODE] Simulating identity check and gaze for '{username}' (ID: {user_id})...")
            await asyncio.sleep(1.2)
            await self._log("SUCCESS", f"[TEST_MODE] Successfully simulated and verified like-back for '{username}' (ID: {user_id}).")
            return True, f"[TEST_MODE] Successfully simulated like-back for '{username}'", "COMPLETED"

        # 2. Reject paywalled fan previews lacking direct platform member ID
        if user_info.get("is_paywalled_preview") or user_id.startswith("fan_preview_") or user_id.startswith("fan_"):
            err_msg = "Paywalled fan preview does not expose direct platform member ID. Action blocked to prevent misidentification."
            await self._log("WARN", f"[IDENTITY_FAILED] {err_msg}")
            return False, err_msg, "IDENTITY_FAILED"

        # 3. Check authentication status
        async with self._lock:
            await self._log("INFO", f"Verifying identity and preparing like-back for '{username}' (ID: {user_id})...")
            context = await self.get_context()
            page = await context.new_page()

            try:
                # Navigate directly to candidate match URL
                target_match_url = f"https://mingle2.com/mutual_match/match/{user_id}"
                await page.goto(target_match_url, wait_until="domcontentloaded", timeout=settings.request_timeout_seconds * 1000)
                await asyncio.sleep(1.5)
                await dismiss_page_overlays(page)

                current_url = strip_google_vignette(page.url.lower())
                if "/user/login" in current_url or any(x in current_url for x in ["login", "sign_in", "step1", "register"]):
                    self.session_status = "LOGGED_OUT"
                    await page.close()
                    await self._log("WARN", f"Session expired while liking '{username}'! Redirected to login.")
                    return False, "Session expired (redirected to /user/login)", "SESSION_EXPIRED"

                if "/user/deactivated" in current_url:
                    await page.close()
                    await self._log("WARN", f"Profile '{username}' (ID: {user_id}) is deactivated.")
                    return False, "Candidate profile is deactivated on Mingle2", "PERMANENT_ERROR"

                # 4. Strict Candidate Identity Verification
                candidate_info = await page.evaluate(r"""() => {
                    const form = document.querySelector('.rate-yes-btn form, form[action*="rate"]');
                    const nameEl = document.querySelector('h1, h2, h3, .name, .username');
                    const badge = document.querySelector('.annotation, .dropbtn .annotation, a[href*="mutual_match"] .badge');
                    return {
                        formAction: form ? form.action : null,
                        name: nameEl ? nameEl.innerText.trim() : 'Unknown',
                        badge: badge ? badge.innerText.trim() : null
                    };
                }""")

                form_action = candidate_info.get("formAction")
                initial_badge = candidate_info.get("badge")

                if not form_action:
                    await page.close()
                    await self._log("WARN", f"Rate form not found on match page for candidate '{username}' (ID: {user_id}).")
                    return False, "Candidate rating form not found on Mingle2 page", "EXECUTION_FAILED"

                # Extract candidate ID from form action: /mutual_match/rate/<candidateId>
                m_action = re.search(r"/rate/(\d+)", form_action)
                actual_candidate_id = m_action.group(1) if m_action else None

                if actual_candidate_id and str(actual_candidate_id) != str(user_id):
                    await page.close()
                    mismatch_msg = f"PROFILE IDENTITY MISMATCH: Expected candidate ID '{user_id}', but match page displays candidate '{candidate_info.get('name')}' (ID: '{actual_candidate_id}'). Action blocked."
                    await self._log("ERROR", mismatch_msg)
                    return False, mismatch_msg, "IDENTITY_FAILED"

                await self._log("INFO", f"Candidate identity verified: '{username}' (ID: {user_id}). Executing like rating...")

                # 5. Execute Rating POST
                rate_res = await page.evaluate(r"""async () => {
                    try {
                        const form = document.querySelector('.rate-yes-btn form, form[action*="rate"]');
                        if (!form) return { status: 0, body: '', error: 'no form' };
                        const tokenInput = form.querySelector('input[name="authenticity_token"]');
                        const token = tokenInput ? tokenInput.value : '';
                        const fd = new FormData();
                        fd.append('utf8', '✓');
                        fd.append('authenticity_token', token);
                        fd.append('rating', 'Y');

                        const resp = await fetch(form.action, {
                            method: 'POST',
                            body: fd,
                            headers: { 'X-Requested-With': 'XMLHttpRequest' }
                        });
                        const body = await resp.text();
                        return { status: resp.status, body: body };
                    } catch(e) {
                        return { status: 0, body: '', error: e.toString() };
                    }
                }""")

                resp_status = rate_res.get("status", 0)
                resp_body = rate_res.get("body", "")
                payload_validation = verify_like_back_response(resp_body) if resp_body else {"action_applied": False, "is_match": False}
                action_confirmed_by_payload = (resp_status == 200 and payload_validation["action_applied"])

                if not action_confirmed_by_payload:
                    # Fallback to DOM button click
                    like_btn = page.locator('.rate-yes-btn button[type="submit"], .rate-yes-btn').first
                    if await like_btn.count() > 0:
                        await like_btn.click(force=True)
                        await asyncio.sleep(2.0)
                    elif resp_status != 200:
                        await page.close()
                        return False, f"Rating request failed (status {resp_status})", "EXECUTION_FAILED"

                # 6. VERIFYING Step: Proof of completion
                await self._log("INFO", f"Action sent for '{username}' (ID: {user_id}). Verifying completion on platform...")
                await asyncio.sleep(2.0)

                # Verify by checking if candidate advanced or if reloading the candidate page shows already rated
                await page.goto(target_match_url, wait_until="domcontentloaded", timeout=settings.request_timeout_seconds * 1000)
                await asyncio.sleep(1.5)
                new_form_action = await page.evaluate(r"""() => {
                    const f = document.querySelector('.rate-yes-btn form, form[action*="rate"]');
                    return f ? f.action : null;
                }""")

                # If the candidate URL is re-visited and the form action changed (or page redirected), the rating was accepted!
                candidate_advanced = (new_form_action != form_action)

                await page.close()

                if action_confirmed_by_payload or candidate_advanced:
                    match_str = " (MUTUAL MATCH CONFIRMED!)" if payload_validation.get("is_match") else ""
                    await self._log("SUCCESS", f"LIKE BACK COMPLETED ✓ for '{username}' (ID: {user_id}){match_str}. Action verified.")
                    return True, f"Like-back confirmed and verified for '{username}' (ID: {user_id}){match_str}", "COMPLETED"
                else:
                    await self._log("WARN", f"LIKE BACK UNVERIFIED for '{username}' (ID: {user_id}). Server payload did not confirm.")
                    return False, "Like-back unverified: Platform payload did not confirm rating completion", "VERIFICATION_FAILED"

            except Exception as e:
                await self._log("ERROR", f"Error during like-back execution for '{username}': {e}")
                try:
                    await page.close()
                except Exception:
                    pass
                return False, str(e), "TEMPORARY_ERROR"

    async def fetch_account_profile(self) -> Dict[str, Any]:
        """
        Navigate to Mingle2 user profile (/user/my_profile) and extract user's
        name, age, location, headline, about_me, avatar, photos, and statistics.
        Saves result in database and returns profile dictionary.
        """
        async with self._lock:
            await self._log("INFO", "Syncing Mingle2 Account Profile (/user/my_profile)...")
            context = await self.get_context()
            page = await context.new_page()

            try:
                profile_url = "https://mingle2.com/user/my_profile"
                await page.goto(profile_url, wait_until="domcontentloaded", timeout=30000)

                current_url = strip_google_vignette(page.url.lower())
                if "/user/login" in current_url or any(x in current_url for x in ["login", "sign_in", "step1"]):
                    self.session_status = "LOGGED_OUT"
                    await self._log("WARN", "Cannot sync account: Not logged into Mingle2.")
                    await page.close()
                    # Return cached profile if exists
                    cached = await database.get_account_profile()
                    return cached or {"user_id": "guest", "username": "Not Logged In", "status": "LOGGED_OUT"}

                self.session_status = "LOGGED_IN"
                await asyncio.sleep(1.5)

                profile_data = await page.evaluate(r"""
                    () => {
                        const url = window.location.href;
                        const usernameEl = document.querySelector('h1.username, .profile-username, .user-name, h1, .name');
                        const username = usernameEl ? usernameEl.innerText.trim() : 'Mingle2 Member';

                        const headlineEl = document.querySelector('.headline, .profile-headline, h2.headline, .tagline');
                        const headline = headlineEl ? headlineEl.innerText.trim() : '';

                        const aboutMeEl = document.querySelector('.about-me, #about_me, .profile-bio, .user-description, .bio-content');
                        const aboutMe = aboutMeEl ? aboutMeEl.innerText.trim() : '';

                        const metaEl = document.querySelector('.user_details, .age-location, .profile-meta, .details');
                        const ageLoc = metaEl ? metaEl.innerText.replace(/\s+/g, ' ').trim() : '';

                        let age = null;
                        const ageMatch = ageLoc.match(/(\d{2})/);
                        if (ageMatch) age = parseInt(ageMatch[1]);

                        const avatarImg = document.querySelector('.profile-avatar img, .avatar img, #profile_pic, img.user-pic, img[src*="/photos/"]');
                        const avatarUrl = avatarImg ? (avatarImg.getAttribute('data-src') || avatarImg.src) : '';

                        const photoEls = Array.from(document.querySelectorAll('.photo-gallery img, .photos img, a[href*="/photos/"] img'));
                        const photos = photoEls.map(img => img.getAttribute('data-src') || img.src).filter(Boolean);

                        const idMatch = url.match(/(?:user\/view|profile|my_profile)\/([a-zA-Z0-9_-]+)/i);
                        const userId = idMatch ? idMatch[1] : 'my_profile';

                        return {
                            user_id: userId,
                            username: username,
                            headline: headline,
                            about_me: aboutMe,
                            age: age,
                            location: ageLoc,
                            avatar_url: avatarUrl,
                            photos: photos,
                            stats: { synced: true }
                        };
                    }
                """)

                await page.close()
                saved = await database.save_account_profile(profile_data)
                await self._log("SUCCESS", f"Account profile successfully synced for '{saved.get('username')}'.")
                return saved

            except Exception as e:
                await self._log("ERROR", f"Error syncing account profile: {e}")
                try:
                    await page.close()
                except Exception:
                    pass
                cached = await database.get_account_profile()
                return cached or {"user_id": "error", "username": "Sync Error", "error": str(e)}

    async def fetch_inbox_threads(self) -> List[Dict[str, Any]]:
        """
        Navigate to https://mingle2.com/inbox/list and scrape active conversations.
        Saves threads in database and returns list.
        """
        async with self._lock:
            await self._log("INFO", "Syncing Mingle2 Mailbox (/inbox/list)...")
            context = await self.get_context()
            page = await context.new_page()

            try:
                inbox_url = "https://mingle2.com/inbox/list"
                await page.goto(inbox_url, wait_until="domcontentloaded", timeout=30000)

                current_url = strip_google_vignette(page.url.lower())
                if "/user/login" in current_url or any(x in current_url for x in ["login", "sign_in"]):
                    self.session_status = "LOGGED_OUT"
                    await self._log("WARN", "Cannot sync inbox: Not logged in.")
                    await page.close()
                    return await database.get_inbox_threads()

                self.session_status = "LOGGED_IN"
                await asyncio.sleep(1.5)

                raw_threads = await page.evaluate(r"""
                    () => {
                        const threads = [];
                        const seen = new Set();
                        const items = document.querySelectorAll(
                            '.conversation-item, .inbox-item, .mail-item, tr.message, .thread-row, ' +
                            'div[class*="conversation"], div[class*="message-item"], li[class*="thread"]'
                        );

                        function parseItem(el) {
                            const link = el.querySelector('a[href*="/inbox/conversation/"], a[href*="/inbox/mail_detail/"], a[href*="/inbox/"]');
                            if (!link) return null;
                            const href = link.href;
                            const match = href.match(/conversation\/([a-zA-Z0-9_-]+)/i) || href.match(/mail_detail\/([a-zA-Z0-9_-]+)/i);
                            const userId = match ? match[1] : (el.getAttribute('data-user-id') || null);
                            if (!userId || seen.has(userId)) return null;
                            seen.add(userId);

                            const nameEl = el.querySelector('.username, .name, strong, h3, h4');
                            const username = nameEl ? nameEl.innerText.trim() : 'User ' + userId;

                            const img = el.querySelector('img');
                            const avatar = img ? (img.getAttribute('data-src') || img.src) : '';

                            const snippetEl = el.querySelector('.snippet, .preview, .last-message, p, .body');
                            const lastMessage = snippetEl ? snippetEl.innerText.replace(/\s+/g, ' ').trim() : '';

                            const timeEl = el.querySelector('.time, .date, .timestamp, small');
                            const time = timeEl ? timeEl.innerText.trim() : '';

                            const isUnread = !!(el.querySelector('.unread, .new, .badge-unread') || el.classList.contains('unread'));

                            return {
                                user_id: userId,
                                username: username,
                                avatar_url: avatar,
                                last_message: lastMessage,
                                last_message_time: time,
                                unread: isUnread
                            };
                        }

                        items.forEach(el => {
                            const t = parseItem(el);
                            if (t) threads.push(t);
                        });

                        // Fallback: search links directly
                        if (threads.length === 0) {
                            const links = document.querySelectorAll('a[href*="/inbox/conversation/"], a[href*="/inbox/mail_detail/"]');
                            links.forEach(l => {
                                const m = l.href.match(/(?:conversation|mail_detail)\/([a-zA-Z0-9_-]+)/i);
                                if (m && !seen.has(m[1])) {
                                    seen.add(m[1]);
                                    threads.push({
                                        user_id: m[1],
                                        username: l.innerText.trim() || 'User ' + m[1],
                                        avatar_url: '',
                                        last_message: '',
                                        last_message_time: '',
                                        unread: false
                                    });
                                }
                            });
                        }
                        return threads;
                    }
                """)

                await page.close()
                if raw_threads:
                    await database.save_inbox_threads(raw_threads)
                await self._log("SUCCESS", f"Inbox synced. Found {len(raw_threads)} conversation(s).")
                return await database.get_inbox_threads()

            except Exception as e:
                await self._log("ERROR", f"Error syncing inbox: {e}")
                try:
                    await page.close()
                except Exception:
                    pass
                return await database.get_inbox_threads()

    async def fetch_thread_messages(self, user_id: str) -> List[Dict[str, Any]]:
        """
        Navigate to /inbox/conversation/{user_id} and extract message history.
        Saves messages in database and returns list.
        """
        async with self._lock:
            await self._log("INFO", f"Fetching chat history with user {user_id}...")
            context = await self.get_context()
            page = await context.new_page()

            try:
                target_url = f"https://mingle2.com/inbox/conversation/{user_id}"
                await page.goto(target_url, wait_until="domcontentloaded", timeout=30000)

                current_url = strip_google_vignette(page.url.lower())
                if "/user/login" in current_url:
                    self.session_status = "LOGGED_OUT"
                    await page.close()
                    return await database.get_thread_messages(user_id)

                await asyncio.sleep(1.5)

                msgs = await page.evaluate(r"""
                    () => {
                        const msgs = [];
                        const elements = document.querySelectorAll(
                            '.chat-message, .message, .chat-bubble, .speech-bubble, div[class*="bubble"], div[class*="msg"]'
                        );
                        elements.forEach(el => {
                            const text = el.innerText.replace(/\s+/g, ' ').trim();
                            if (!text || text.length < 2) return;
                            const isSentByMe = el.classList.contains('sent') ||
                                               el.classList.contains('outgoing') ||
                                               el.classList.contains('me') ||
                                               el.classList.contains('from-me') ||
                                               !!el.closest('.sent, .outgoing, .me');
                            const sender = isSentByMe ? 'You' : 'Them';
                            const timeEl = el.querySelector('.time, .timestamp, small');
                            const sentAt = timeEl ? timeEl.innerText.trim() : '';
                            msgs.push({
                                sender: sender,
                                message_body: text,
                                sent_at: sentAt
                            });
                        });
                        return msgs;
                    }
                """)

                await page.close()
                if msgs:
                    await database.save_thread_messages(user_id, msgs)
                return await database.get_thread_messages(user_id)

            except Exception as e:
                await self._log("ERROR", f"Error fetching conversation with {user_id}: {e}")
                try:
                    await page.close()
                except Exception:
                    pass
                return await database.get_thread_messages(user_id)

    async def send_inbox_message(self, user_id: str, message_text: str) -> Tuple[bool, str]:
        """
        Send a direct message / reply to user_id on Mingle2.
        Types message with human delay and clicks submit.
        """
        if not message_text or not message_text.strip():
            return False, "Message cannot be empty"

        async with self._lock:
            await self._log("INFO", f"Sending message to user {user_id}...")
            context = await self.get_context()
            page = await context.new_page()

            try:
                target_url = f"https://mingle2.com/inbox/conversation/{user_id}"
                await page.goto(target_url, wait_until="domcontentloaded", timeout=30000)

                current_url = strip_google_vignette(page.url.lower())
                if "/user/login" in current_url:
                    self.session_status = "LOGGED_OUT"
                    await page.close()
                    return False, "Not authenticated on Mingle2"

                # Locate textarea
                input_selector = None
                for sel in ["#message_body", "textarea[name='body']", "textarea[name='message']", "textarea"]:
                    if await page.locator(sel).count() > 0:
                        input_selector = sel
                        break

                if not input_selector:
                    await page.close()
                    return False, "Message input textarea not found on page"

                # Type message with human-like delay
                await page.focus(input_selector)
                await page.type(input_selector, message_text, delay=random.randint(25, 60))
                await asyncio.sleep(random.uniform(0.5, 1.2))

                # Submit form
                send_btn = None
                for sel in ["#send_message", "button[type='submit']", "input[type='submit']", ".send-btn", ".btn-send"]:
                    elem = page.locator(sel).first
                    if await elem.count() > 0:
                        send_btn = elem
                        break

                if send_btn:
                    await send_btn.click()
                else:
                    await page.press(input_selector, "Enter")

                await asyncio.sleep(2.0)
                await page.close()

                # Add to local DB
                await database.add_inbox_message(user_id, "You", message_text)
                await self._log("SUCCESS", f"Message sent successfully to user {user_id}.")
                return True, "Message sent successfully"

            except Exception as e:
                await self._log("ERROR", f"Failed to send message to {user_id}: {e}")
                try:
                    await page.close()
                except Exception:
                    pass
                return False, str(e)

    async def run_search(self, params: Dict[str, Any]) -> List[Dict[str, Any]]:
        """
        Execute basic or advanced search on Mingle2 (/search).
        Extracts matching user profiles and caches them in search_cache.
        """
        async with self._lock:
            await self._log("INFO", f"Running Mingle2 search with params: {params}...")
            context = await self.get_context()
            page = await context.new_page()

            try:
                search_url = "https://mingle2.com/search"
                await page.goto(search_url, wait_until="domcontentloaded", timeout=30000)

                current_url = strip_google_vignette(page.url.lower())
                if "/user/login" in current_url:
                    self.session_status = "LOGGED_OUT"
                    await page.close()
                    return await database.get_cached_search_results("SEARCH")

                # Fill preferences if form fields present
                try:
                    looking_for = params.get("looking_for")
                    if looking_for:
                        val = looking_for.lower()
                        # Looking for M/F select
                        for sel in ["select[name='preferences[looking_for]']", "select#preferences_looking_for"]:
                            if await page.locator(sel).count() > 0:
                                await page.select_option(sel, value=val)

                    age_from = params.get("age_from")
                    if age_from:
                        for sel in ["select[name='preferences[age_from]']", "input[name='preferences[age_from]']"]:
                            if await page.locator(sel).count() > 0:
                                await page.fill(sel, str(age_from))

                    age_to = params.get("age_to")
                    if age_to:
                        for sel in ["select[name='preferences[age_to]']", "input[name='preferences[age_to]']"]:
                            if await page.locator(sel).count() > 0:
                                await page.fill(sel, str(age_to))

                    country = params.get("country_id")
                    if country:
                        for sel in ["select[name='preferences[country_id]']"]:
                            if await page.locator(sel).count() > 0:
                                await page.select_option(sel, value=str(country))

                    keywords = params.get("keywords")
                    if keywords:
                        for sel in ["input[name='keywords']", "input[name='q']", "#search_keywords"]:
                            if await page.locator(sel).count() > 0:
                                await page.fill(sel, str(keywords))
                except Exception as fill_err:
                    logger.debug(f"Form fill non-fatal notice: {fill_err}")

                # Submit search
                submit_btn = page.locator("input[type='submit'][value*='Search'], button:has-text('Search'), input.btn-search").first
                if await submit_btn.count() > 0:
                    await submit_btn.click()
                else:
                    await page.keyboard.press("Enter")

                await asyncio.sleep(2.5)

                results = await page.evaluate(r"""
                    () => {
                        const items = [];
                        const seen = new Set();
                        const cards = document.querySelectorAll(
                            '.search-result, .user-card, .profile-card, .user_item, div[id^="user_"], .card'
                        );

                        function parseCard(el) {
                            const link = el.querySelector('a[href*="/users/view/"], a[href*="/user/view/"], a[href*="/profile/"]');
                            if (!link) return null;
                            const href = link.href;
                            const match = href.match(/(?:users\/view|user\/view|profile)\/([a-zA-Z0-9_-]+)/i);
                            const userId = match ? match[1] : null;
                            if (!userId || seen.has(userId)) return null;
                            seen.add(userId);

                            const nameEl = el.querySelector('.username, .name, strong, h3, h4') || link;
                            const username = nameEl ? nameEl.innerText.trim() : 'User ' + userId;

                            const img = el.querySelector('img');
                            const avatar = img ? (img.getAttribute('data-src') || img.src) : '';

                            const detailsEl = el.querySelector('.user_details, .details, .meta, p');
                            const ageLoc = detailsEl ? detailsEl.innerText.replace(/\s+/g, ' ').trim() : '';

                            const isOnline = !!(el.querySelector('.online, .status-online, .badge-online, .green-dot'));

                            return {
                                user_id: userId,
                                platform_profile_id: userId,
                                username: username,
                                display_name: username,
                                age_location: ageLoc,
                                location: ageLoc,
                                avatar_url: avatar,
                                photo_reference: avatar,
                                profile_url: `https://mingle2.com/users/view/${userId}`,
                                is_online: isOnline
                            };
                        }

                        cards.forEach(c => {
                            const p = parseCard(c);
                            if (p) items.push(p);
                        });

                        return items;
                    }
                """)

                await page.close()

                # Apply explainable ranking, profession taxonomy expansion, and multi-factor scoring
                ranked_results = rank_and_filter_search_results(
                    candidates=results,
                    query_profession=params.get("profession") or params.get("keywords"),
                    query_location=params.get("location"),
                    query_keywords=params.get("keywords"),
                    age_min=params.get("age_from"),
                    age_max=params.get("age_to"),
                )

                output_list = [r.to_dict() for r in ranked_results]

                # Cache in search_cache and persist normalized profile records
                await database.save_search_results(output_list, "SEARCH")
                for r in ranked_results:
                    p = Profile(
                        platform_profile_id=r.platform_profile_id,
                        profile_url=r.profile_url,
                        display_name=r.display_name,
                        age=r.age,
                        location=r.location,
                        profession=r.profession,
                        photo_reference=r.photo_reference,
                        source="SEARCH",
                        verification_status="UNVERIFIED",
                    )
                    await database.upsert_profile(p)

                await self._log("SUCCESS", f"Search completed. Found {len(output_list)} ranked match(es).")
                return output_list

            except Exception as e:
                await self._log("ERROR", f"Error during search: {e}")
                try:
                    await page.close()
                except Exception:
                    pass
                return await database.get_cached_search_results("SEARCH")

    async def fetch_online_users(self, limit: int = 30) -> List[Dict[str, Any]]:
        """
        Query currently active / online users on Mingle2 (/online or /search).
        Caches results in search_cache with search_type='ONLINE'.
        """
        async with self._lock:
            await self._log("INFO", "Scanning Mingle2 for currently Online Users...")
            context = await self.get_context()
            page = await context.new_page()

            try:
                # Try /online or browse active users
                target_url = "https://mingle2.com/online"
                await page.goto(target_url, wait_until="domcontentloaded", timeout=30000)

                current_url = strip_google_vignette(page.url.lower())
                if "/user/login" in current_url:
                    self.session_status = "LOGGED_OUT"
                    await page.close()
                    return await database.get_cached_search_results("ONLINE")

                await asyncio.sleep(2.0)

                online_users = await page.evaluate(r"""
                    () => {
                        const items = [];
                        const seen = new Set();
                        const cards = document.querySelectorAll(
                            '.online-user, .user-card, .profile-card, div[id^="user_"], .user_item'
                        );

                        function parseCard(el) {
                            const link = el.querySelector('a[href*="/users/view/"], a[href*="/user/view/"], a[href*="/profile/"]');
                            if (!link) return null;
                            const href = link.href;
                            const match = href.match(/(?:users\/view|user\/view|profile)\/([a-zA-Z0-9_-]+)/i);
                            const userId = match ? match[1] : null;
                            if (!userId || seen.has(userId)) return null;
                            seen.add(userId);

                            const nameEl = el.querySelector('.username, .name, strong, h3') || link;
                            const username = nameEl ? nameEl.innerText.trim() : 'User ' + userId;

                            const img = el.querySelector('img');
                            const avatar = img ? (img.getAttribute('data-src') || img.src) : '';

                            const detailsEl = el.querySelector('.user_details, .details, .meta, p');
                            const ageLoc = detailsEl ? detailsEl.innerText.replace(/\s+/g, ' ').trim() : '';

                            let age = null;
                            let gender = null;
                            const mAge = ageLoc.match(/(?:^|[,\s•])(\d{2})(?:[,\s•]|$)/);
                            if (mAge) age = parseInt(mAge[1]);
                            if (/\b(?:female|woman|girl|f)\b/i.test(ageLoc)) gender = 'F';
                            else if (/\b(?:male|man|guy|m)\b/i.test(ageLoc)) gender = 'M';

                            return {
                                user_id: userId,
                                username: username,
                                age: age,
                                gender: gender,
                                age_location: ageLoc,
                                avatar_url: avatar,
                                profile_url: `https://mingle2.com/users/view/${userId}`,
                                is_online: true
                            };
                        }

                        cards.forEach(c => {
                            const p = parseCard(c);
                            if (p) items.push(p);
                        });

                        return items;
                    }
                """)

                await page.close()
                await database.save_search_results(online_users, "ONLINE")
                await self._log("SUCCESS", f"Online scan complete. Found {len(online_users)} active user(s).")
                return online_users

            except Exception as e:
                await self._log("ERROR", f"Error scanning online users: {e}")
                try:
                    await page.close()
                except Exception:
                    pass
                return await database.get_cached_search_results("ONLINE")
