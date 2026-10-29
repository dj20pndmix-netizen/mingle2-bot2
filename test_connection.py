# test_connection.py
import asyncio
import os
import sys
from pathlib import Path

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
from src.config import settings

BROWSER_DATA_DIR = Path("./browser_data").resolve()

async def diagnose():
    print("[*] Starting Mingle2 Connection Diagnostic...")
    async with async_playwright() as p:
        # Launch with persistent context to check existing session/cookies
        context = await p.chromium.launch_persistent_context(
            user_data_dir=str(BROWSER_DATA_DIR),
            headless=False,  # Launch visible to observe behavior
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36",
            viewport={"width": 1280, "height": 800},
            args=[
                "--disable-blink-features=AutomationControlled",
                "--no-sandbox",
                "--disable-infobars"
            ]
        )

        # Apply ad & vignette blocking
        await setup_ad_blocking(context)

        page = context.pages[0] if context.pages else await context.new_page()

        # Stealth: Override navigator.webdriver
        await page.add_init_script("""
            Object.defineProperty(navigator, 'webdriver', {
                get: () => undefined
            });
        """)

        try:
            print("[*] Attempting navigation to https://mingle2.com ...")
            response = await page.goto("https://mingle2.com", wait_until="domcontentloaded", timeout=30000)
            
            print(f"[+] HTTP Status: {response.status if response else 'No Response'}")
            print(f"[+] Current URL: {page.url}")
            print(f"[+] Page Title: {await page.title()}")

            # Check for Cloudflare / bot block
            content = await page.content()
            clean_page_url = strip_google_vignette(page.url.lower())
            if "cf-browser-verification" in content or "Attention Required!" in content or "Cloudflare" in await page.title():
                print("[!] BLOCKED: Cloudflare / Bot detection challenge triggered.")
                await page.screenshot(path="data/cloudflare_block.png")
            elif "/user/login" in clean_page_url or "sign_in" in clean_page_url or "step1" in clean_page_url or "nexturl" in clean_page_url:
                print("[*] Connected successfully, but user is currently logged out (redirected to /user/login).")
                await page.screenshot(path="data/sign_in_page.png")
            else:
                print("[+] Connection SUCCESSFUL! Site loaded.")
                await page.screenshot(path="data/mingle2_success.png")

            # Test the target endpoint
            print("[*] Testing target URL: https://mingle2.com/mutual_match/who_is_interested_in_me ...")
            target_response = await page.goto("https://mingle2.com/mutual_match/who_is_interested_in_me", wait_until="domcontentloaded", timeout=30000)
            print(f"[+] Target Page URL: {page.url}")
            await page.screenshot(path="data/target_page.png")

        except Exception as e:
            print(f"[!] Error during navigation: {type(e).__name__}: {e}")
            try:
                await page.screenshot(path="data/error_state.png")
            except Exception:
                pass
        finally:
            print("[*] Diagnostic complete. Check screenshot files in ./data/ for visual evidence.")
            await context.close()

if __name__ == "__main__":
    asyncio.run(diagnose())
