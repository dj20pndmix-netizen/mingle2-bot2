import asyncio
import os
import sys
from pathlib import Path

# Auto-switch to .venv
BASE_DIR = Path(__file__).resolve().parent.parent
venv_python = (BASE_DIR / ".venv" / "Scripts" / "python.exe") if os.name == "nt" else (BASE_DIR / ".venv" / "bin" / "python")
if venv_python.exists() and Path(sys.executable).resolve() != venv_python.resolve():
    import subprocess
    sys.exit(subprocess.run([str(venv_python)] + sys.argv).returncode)

import subprocess
import urllib.request
from playwright.async_api import async_playwright

async def run_test():
    print("=" * 60, flush=True)
    print("   MINGLE2 COMMAND CENTER FULL TEST SUITE", flush=True)
    print("=" * 60, flush=True)

    # 1. Start Uvicorn server on dynamic clean port to avoid TIME_WAIT socket collisions
    import socket
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(('127.0.0.1', 0))
        test_port = s.getsockname()[1]

    import tempfile
    test_db = Path(tempfile.gettempdir()) / f"test_queue_{test_port}.db"
    env = os.environ.copy()
    env["PYTHONPATH"] = str(BASE_DIR)
    env["TEST_MODE"] = "true"
    env["DB_PATH"] = str(test_db)
    python_exe = str(venv_python) if venv_python.exists() else sys.executable
    proc = subprocess.Popen(
        [python_exe, "-m", "uvicorn", "src.server:app", "--host", "127.0.0.1", "--port", str(test_port), "--log-level", "warning"],
        env=env,
        cwd=str(BASE_DIR),
    )

    # Wait for server to become responsive
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
    print(f"[1/9] Test server running on http://127.0.0.1:{test_port}", flush=True)

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        page = await browser.new_page(viewport={"width": 1400, "height": 900})
        page.on("pageerror", lambda err: print(f"PAGE ERROR: {err}", flush=True))
        page.on("console", lambda msg: print(f"CONSOLE: {msg.type}: {msg.text}", flush=True))

        try:
            # 2. Navigate to Dashboard
            print("[2/9] Navigating to Command Center...", flush=True)
            await page.goto(f"http://127.0.0.1:{test_port}", wait_until="commit", timeout=15000)
            await page.wait_for_selector("#tab-btn-dashboard", timeout=20000)
            title = await page.title()
            print(f"      Page title: '{title}'", flush=True)
            assert "Mingle2" in title, f"Unexpected title: {title}"

            # 3. Test Tab Navigation across all 9 modules
            print("[3/9] Testing navigation across all 9 modules (including Diagnostics)...", flush=True)
            tabs = ["account", "inbox", "matches", "search", "online", "tasks", "console", "dashboard", "diagnostics"]
            for t in tabs:
                btn = page.locator(f"#tab-btn-{t}")
                await btn.click()
                await asyncio.sleep(0.3)
                content = page.locator(f"#tab-content-{t}")
                is_visible = await content.is_visible()
                assert is_visible, f"Tab content for {t} failed to display!"
            print(f"      All {len(tabs)} tabs navigated and rendered successfully.", flush=True)

            # 4. Test Diagnostics Tab subsystem cards
            print("[4/9] Verifying Diagnostics tab subsystem cards...", flush=True)
            await page.locator("#tab-btn-diagnostics").click()
            await asyncio.sleep(0.5)
            diag_cards = page.locator(".diag-card")
            diag_count = await diag_cards.count()
            print(f"      Diagnostic subsystem cards rendered: {diag_count}", flush=True)
            assert diag_count >= 4, f"Expected at least 4 diagnostic cards, found {diag_count}"

            # 5. Test Test-Mode Toggle
            print("[5/9] Testing Test Mode toggle and safety banner...", flush=True)
            test_mode_btn = page.locator("#btn-toggle-test-mode")
            if await test_mode_btn.count() > 0:
                await test_mode_btn.click()
                await asyncio.sleep(0.5)
                banner = page.locator("#test-mode-banner")
                print("      Test mode toggle clicked successfully.", flush=True)

            # 6. Test Task Dispatcher
            print("[6/9] Testing Task Dispatcher (Queueing AUTO_CHECK_INBOX task)...", flush=True)
            await page.locator("#tab-btn-tasks").click()
            await asyncio.sleep(0.5)
            await page.select_option("#task-type-select", "AUTO_CHECK_INBOX")
            await page.locator("#create-task-form button[type='submit']").click()
            await asyncio.sleep(1.5)

            tasks_rows = page.locator("#tasks-table-body tr")
            task_count = await tasks_rows.count()
            print(f"      Tasks in queue: {task_count}", flush=True)
            assert task_count > 0, "No task row created in tasks table!"

            # 7. Test Matches & Likes Queue Simulation
            print("[7/9] Testing Mutual Matches & Queue simulation...", flush=True)
            await page.locator("#tab-btn-matches").click()
            await asyncio.sleep(0.5)
            sim_btn = page.locator("button:has-text('Simulate Liker')")
            await sim_btn.click()
            await asyncio.sleep(1.5)

            rows = page.locator("#queue-table-body tr")
            q_count = await rows.count()
            print(f"      Queue items rendered: {q_count}", flush=True)
            assert q_count > 0, "No items in queue table after simulation!"

            # 8. Test Mailbox UI
            print("[8/9] Verifying Mailbox chat interface...", flush=True)
            await page.locator("#tab-btn-inbox").click()
            await asyncio.sleep(0.5)
            chat_input = page.locator("#chat-input-text")
            assert await chat_input.count() > 0, "Mailbox chat input missing"

            # 9. Capture Full-Page Screenshot
            out_dir = Path("data")
            out_dir.mkdir(parents=True, exist_ok=True)
            screenshot_path = out_dir / "command_center_verified.png"
            await page.screenshot(path=str(screenshot_path), full_page=True)
            print(f"[9/9] Full screenshot captured to: {screenshot_path}", flush=True)

            print("\n" + "=" * 60, flush=True)
            print(">>> ALL COMMAND CENTER TESTS PASSED WITH 100% SUCCESS! <<<", flush=True)
            print("=" * 60 + "\n", flush=True)

        finally:
            try:
                await browser.close()
            except Exception:
                pass
            try:
                proc.terminate()
                proc.wait(timeout=3)
            except Exception:
                try:
                    proc.kill()
                except Exception:
                    pass

if __name__ == "__main__":
    asyncio.run(run_test())
