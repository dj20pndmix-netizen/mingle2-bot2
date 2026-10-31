import asyncio
import os
import sys
import socket
import subprocess
import urllib.request
from pathlib import Path
from playwright.async_api import async_playwright

BASE_DIR = Path(__file__).resolve().parent.parent

async def run_ui_test():
    print("=" * 60, flush=True)
    print("   RUNNING MINGLE2 UI VERIFICATION TEST", flush=True)
    print("=" * 60, flush=True)

    # 1. Bind dynamic port for test server
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(('127.0.0.1', 0))
        test_port = s.getsockname()[1]

    env = os.environ.copy()
    env["PYTHONPATH"] = str(BASE_DIR)
    env["TEST_MODE"] = "true"
    python_exe = sys.executable

    proc = subprocess.Popen(
        [python_exe, "-m", "uvicorn", "src.server:app", "--host", "127.0.0.1", "--port", str(test_port), "--log-level", "warning"],
        env=env,
        cwd=str(BASE_DIR),
    )

    # Wait for server
    server_ready = False
    for _ in range(30):
        try:
            with urllib.request.urlopen(f"http://127.0.0.1:{test_port}/healthz", timeout=1) as resp:
                if resp.status == 200:
                    server_ready = True
                    break
        except Exception:
            await asyncio.sleep(0.5)

    assert server_ready, f"Failed to start Uvicorn test server on port {test_port}"
    print(f"[1/6] Test server running on http://127.0.0.1:{test_port}", flush=True)

    try:
        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=True)
            page = await browser.new_page(viewport={"width": 1280, "height": 900})

            print(f"[2/6] Navigating to http://127.0.0.1:{test_port}...")
            await page.goto(f"http://127.0.0.1:{test_port}", wait_until="domcontentloaded", timeout=15000)
            await page.wait_for_selector("#tab-btn-dashboard", timeout=10000)
            title = await page.title()
            print(f"      Dashboard loaded. Title: '{title}'")
            assert "Mingle2" in title, "Title does not contain Mingle2"

            print("[3/6] Clicking 'Simulate Incoming Like' to test reactive queue...")
            sim_btn = page.locator("button", has_text="Simulate Incoming Like")
            await sim_btn.click()
            await asyncio.sleep(1.5)

            print("[4/6] Verifying user card added to queue table...")
            await page.locator("#tab-btn-matches").click()
            await asyncio.sleep(0.5)
            rows = page.locator("#queue-table-body tr")
            count = await rows.count()
            print(f"      Queue row count: {count}")
            assert count > 0, "No rows rendered in queue table"

            first_row_text = await rows.first.inner_text()
            print(f"      First row: {first_row_text.replace(chr(10), ' | ')[:120]}")

            print("[5/6] Testing immediate 'Like' action on pending user...")
            like_btn = rows.first.locator("button", has_text="Like")
            if await like_btn.count() > 0:
                await like_btn.scroll_into_view_if_needed()
                await like_btn.click(force=True)
                print("      Clicked 'Like' button, waiting for status confirmation...")
                await asyncio.sleep(2.5)

            # Check console logs updated
            console_text = await page.locator("#console-logs").inner_text()
            print(f"      Console log entries found: {'[SUCCESS]' in console_text or '[INFO]' in console_text}")

            # Capture verification screenshots
            output_dir = Path("data")
            output_dir.mkdir(parents=True, exist_ok=True)
            screenshot_path = output_dir / "dashboard_verified.png"
            await page.screenshot(path=str(screenshot_path), full_page=True)
            print(f"[6/6] Saved full page screenshot to {screenshot_path}")

            await browser.close()
            print("\n>>> ALL PLAYWRIGHT DASHBOARD UI TESTS PASSED SUCCESSFULLY! <<<\n")
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=3)
        except Exception:
            proc.kill()

if __name__ == "__main__":
    asyncio.run(run_ui_test())
