# test_like_back.py
import asyncio
import os
import sys
from pathlib import Path

# Auto-switch to .venv if running with global python
BASE_DIR = Path(__file__).resolve().parent
venv_python = (BASE_DIR / ".venv" / "Scripts" / "python.exe") if os.name == "nt" else (BASE_DIR / ".venv" / "bin" / "python")
if venv_python.exists() and Path(sys.executable).resolve() != venv_python.resolve():
    import subprocess
    try:
        sys.exit(subprocess.run([str(venv_python)] + sys.argv).returncode)
    except KeyboardInterrupt:
        sys.exit(0)

from playwright.async_api import async_playwright

from src.bot_engine import setup_ad_blocking, strip_google_vignette

BROWSER_DATA_DIR = Path("./browser_data").resolve()
SCREENSHOT_DIR = Path("./data/test_screenshots").resolve()
SCREENSHOT_DIR.mkdir(parents=True, exist_ok=True)

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

    for lock_name in ["lockfile", "SingletonLock", "SingletonCookie", "SingletonSocket"]:
        lock_file = BROWSER_DATA_DIR / lock_name
        if lock_file.exists():
            try:
                lock_file.unlink()
            except Exception:
                pass

async def test_bot():
    print("========================================")
    print("   MINGLE2 LIKE-BACK TEST & DIAGNOSTIC  ")
    print("========================================")
    
    cleanup_stale_processes()
    await asyncio.sleep(0.5)

    async with async_playwright() as p:
        context = await p.chromium.launch_persistent_context(
            user_data_dir=str(BROWSER_DATA_DIR),
            headless=False,  # Visible browser for verification
            viewport={"width": 1280, "height": 800},
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36",
            args=[
                "--disable-blink-features=AutomationControlled",
                "--no-sandbox",
                "--disable-dev-shm-usage",
            ],
            ignore_default_args=["--enable-automation"],
        )

        await context.add_init_script("""
            Object.defineProperty(navigator, 'webdriver', { get: () => undefined });
            window.chrome = { runtime: {} };
            Object.defineProperty(navigator, 'plugins', { get: () => [1, 2, 3, 4, 5] });
            Object.defineProperty(navigator, 'languages', { get: () => ['en-US', 'en'] });
        """)

        page = context.pages[0] if context.pages else await context.new_page()

        # 1. Block Google ads and vignettes
        await setup_ad_blocking(context)

        # 2. Check Login Status
        print("[1/4] Checking login status...")
        target_url = "https://mingle2.com/mutual_match/match"
        await page.goto(target_url, wait_until="commit", timeout=25000)
        await page.wait_for_timeout(2000)

        current_url = strip_google_vignette(page.url.lower())
        if "/user/login" in current_url or "/sign_in" in current_url or "step1" in current_url or "nexturl" in current_url:
            print("\n[FAILED] You are NOT logged in.")
            print("Please run the app, click 'Open Browser for Login', and sign into Mingle2 first.\n")
            await page.screenshot(path=str(SCREENSHOT_DIR / "login_required.png"))
            await context.close()
            return

        print("[SUCCESS] Logged in! Current URL:", page.url)
        await page.screenshot(path=str(SCREENSHOT_DIR / "1_logged_in_page.png"))

        # Dismiss overlays
        from src.bot_engine import dismiss_page_overlays
        await dismiss_page_overlays(page)

        # 3. Detect MutualMatch Candidate & Current Badge
        print("[2/4] Reading MutualMatch candidate and like counter...")
        candidate = await page.evaluate(r"""() => {
            const nameEl = document.querySelector('h1, h2, h3, .name, .username');
            const form = document.querySelector('.rate-yes-btn form');
            const badge = document.querySelector('.annotation, .dropbtn .annotation');
            return {
                name: nameEl ? nameEl.innerText.trim() : 'Unknown',
                formAction: form ? form.action : null,
                badge: badge ? badge.innerText.trim() : 'none'
            };
        }""")

        name = candidate.get("name", "User")
        action = candidate.get("formAction")
        badge = candidate.get("badge", "none")
        print(f"[*] Current candidate: {name}")
        print(f"[*] Incoming likes badge count: {badge}")

        if not action:
            print("[WARN] No like form found on match page. Capturing diagnostic screenshot...")
            await page.screenshot(path=str(SCREENSHOT_DIR / "match_page_no_form.png"))
            await context.close()
            return

        # 4. Perform Like Action
        print(f"[3/4] Attempting to like back candidate '{name}'...")
        rate_success = await page.evaluate(r"""async () => {
            try {
                const form = document.querySelector('.rate-yes-btn form');
                if (!form) return false;
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
                return resp.status === 200;
            } catch(e) {
                return false;
            }
        }""")

        if rate_success:
            print(f"[SUCCESS] Successfully sent like-back rating for '{name}'!")
            await page.wait_for_timeout(2000)
            await page.goto(target_url, wait_until="commit", timeout=25000)
            await page.wait_for_timeout(1500)
            await dismiss_page_overlays(page)

            new_badge = await page.evaluate(r"""() => {
                const badge = document.querySelector('.annotation, .dropbtn .annotation');
                return badge ? badge.innerText.trim() : 'none';
            }""")
            print(f"[*] Likes badge count after like: {new_badge}")
            await page.screenshot(path=str(SCREENSHOT_DIR / "2_after_like.png"))
        else:
            print("[WARN] Fetch rating failed, trying DOM click...")
            like_btn = page.locator('.rate-yes-btn button[type="submit"], .rate-yes-btn').first
            if await like_btn.count() > 0:
                await like_btn.click(force=True)
                await page.wait_for_timeout(2500)
                await page.screenshot(path=str(SCREENSHOT_DIR / "2_after_click.png"))
                print(f"[SUCCESS] Clicked Like button for '{name}'!")

        print("\n[4/4] Test complete! Review screenshot files in ./data/test_screenshots/\n")
        await context.close()

if __name__ == "__main__":
    asyncio.run(test_bot())
