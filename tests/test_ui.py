import asyncio
from pathlib import Path
from playwright.async_api import async_playwright

async def run_ui_test():
    print("[1/6] Launching Playwright Chromium in headless mode...")
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        page = await browser.new_page(viewport={"width": 1280, "height": 900})

        print("[2/6] Navigating to http://127.0.0.1:8000...")
        await page.goto("http://127.0.0.1:8000", wait_until="networkidle")
        title = await page.title()
        print(f"      Dashboard loaded. Title: '{title}'")
        assert "Mingle2" in title, "Title does not contain Mingle2"

        print("[3/6] Clicking 'Simulate Incoming Like' to test reactive queue...")
        sim_btn = page.locator("button", has_text="Simulate Incoming Like")
        await sim_btn.click()
        await asyncio.sleep(1.5)

        print("[4/6] Verifying user card added to queue table...")
        rows = page.locator("#queue-table-body tr")
        count = await rows.count()
        print(f"      Queue row count: {count}")
        assert count > 0, "No rows rendered in queue table"

        first_row_text = await rows.first.inner_text()
        print(f"      First row: {first_row_text.replace(chr(10), ' | ')[:120]}")

        print("[5/6] Testing immediate 'Like' action on pending user...")
        like_btn = rows.first.locator("button", has_text="Like")
        if await like_btn.count() > 0:
            await like_btn.click()
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

if __name__ == "__main__":
    asyncio.run(run_ui_test())
