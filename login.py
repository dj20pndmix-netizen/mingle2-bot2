# login.py
import os
import sys
import time
from pathlib import Path

# Auto-detect and switch to .venv interpreter if running outside virtual environment
BASE_DIR = Path(__file__).resolve().parent
venv_python = (BASE_DIR / ".venv" / "Scripts" / "python.exe") if os.name == "nt" else (BASE_DIR / ".venv" / "bin" / "python")
if venv_python.exists() and Path(sys.executable).resolve() != venv_python.resolve():
    import subprocess
    try:
        sys.exit(subprocess.run([str(venv_python)] + sys.argv).returncode)
    except KeyboardInterrupt:
        sys.exit(0)

from playwright.sync_api import sync_playwright

BROWSER_DATA_DIR = (BASE_DIR / "browser_data").resolve()
BROWSER_DATA_DIR.mkdir(parents=True, exist_ok=True)

def cleanup_stale_processes():
    """Ensure no stale Playwright driver or Chromium instances hold locks on browser_data."""
    ps1 = BASE_DIR / "kill_playwright.ps1"
    if ps1.exists():
        import subprocess
        try:
            subprocess.run(
                ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(ps1)],
                check=False,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=10,
            )
        except Exception:
            pass

    # Ensure any residual lock files are removed
    for lock_name in ["lockfile", "SingletonLock", "SingletonCookie", "SingletonSocket"]:
        lock_file = BROWSER_DATA_DIR / lock_name
        if lock_file.exists():
            try:
                lock_file.unlink()
            except Exception:
                pass

def run_login():
    print("=" * 60)
    print("       MINGLE2 MANUAL LOGIN HELPER")
    print("=" * 60)
    print("[1/3] Preparing persistent browser profile...")

    cleanup_stale_processes()
    time.sleep(0.5)

    print("[2/3] Launching visible stealth browser window...")
    with sync_playwright() as p:
        try:
            browser = p.chromium.launch_persistent_context(
                user_data_dir=str(BROWSER_DATA_DIR),
                headless=False,  # Visible window
                viewport={"width": 1280, "height": 850},
                user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36",
                locale="en-US",
                timezone_id="America/New_York",
                args=[
                    "--start-maximized",
                    "--disable-blink-features=AutomationControlled",
                    "--no-sandbox",
                    "--disable-dev-shm-usage",
                    "--disable-infobars",
                ],
                ignore_default_args=["--enable-automation"],
            )
        except Exception as e:
            print(f"\n[ERROR] Could not launch browser: {e}")
            print("\nIf missing Chromium, run this command in your terminal:")
            print("    playwright install chromium\n")
            return

        # Inject stealth evasion scripts to prevent reCAPTCHA / Cloudflare from detecting automation
        browser.add_init_script("""
            Object.defineProperty(navigator, 'webdriver', { get: () => undefined });
            window.chrome = { runtime: {} };
            Object.defineProperty(navigator, 'plugins', { get: () => [1, 2, 3, 4, 5] });
            Object.defineProperty(navigator, 'languages', { get: () => ['en-US', 'en'] });
        """)

        # Failsafe script for login form: ensures form submits even if reCAPTCHA hangs
        browser.add_init_script("""
            document.addEventListener('DOMContentLoaded', () => {
                const form = document.querySelector('#login-form');
                const btn = document.querySelector('.form-with-recaptcha-action, button[type="submit"]');
                if (form && btn) {
                    btn.addEventListener('click', () => {
                        // If reCAPTCHA doesn't submit the form within 2.5s, force submit directly
                        setTimeout(() => {
                            if (!window.__form_submitted) {
                                window.__form_submitted = true;
                                console.log('[Helper] Executing fallback form submit...');
                                form.submit();
                            }
                        }, 2500);
                    });
                }
            });
        """)

        # Block Google Ads and Vignette overlays
        ad_patterns = [
            "*googleads*",
            "*doubleclick.net*",
            "*googlesyndication*",
            "*adservice.google*",
            "*adnxs*",
            "*taboola*",
            "*outbrain*",
        ]
        for pat in ad_patterns:
            try:
                browser.route(pat, lambda route: route.abort())
            except Exception:
                pass

        page = browser.pages[0] if browser.pages else browser.new_page()

        print("[3/3] Navigating to Mingle2 login page...")
        page.goto("https://mingle2.com/user/login", wait_until="domcontentloaded")

        print("\n" + "=" * 60)
        print("ACTION REQUIRED:")
        print("1. Look at the Chromium window that just opened.")
        print("2. Enter your email/password and click 'LOGIN NOW'.")
        print("3. Once you are successfully logged in on the site,")
        print("   COME BACK TO THIS TERMINAL and press ENTER to save.")
        print("=" * 60 + "\n")

        try:
            input("Press [ENTER] here AFTER you have finished logging in... ")
        except (KeyboardInterrupt, EOFError):
            print("\n[*] Exiting login helper...")

        print("[*] Verifying login status...")
        try:
            page.goto("https://mingle2.com/mutual_match/who_is_interested_in_me", wait_until="domcontentloaded", timeout=20000)
            time.sleep(2)
            current_url = page.url.lower()
            if "/user/login" not in current_url and "/sign_in" not in current_url and "step1" not in current_url:
                print("\n[SUCCESS] Login verified and saved! Cookies permanently stored in ./browser_data.")
            else:
                print("\n[WARNING] Still seeing login page. Please verify your credentials and try again.")
        except Exception as ex:
            print("[*] Saved current session state.")

        browser.close()
        print("[*] Browser closed. You are ready to start the bot!")

if __name__ == "__main__":
    run_login()
