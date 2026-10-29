import asyncio
import json
from pathlib import Path
from playwright.async_api import async_playwright

async def inspect_mutual_match():
    async with async_playwright() as p:
        ctx = await p.chromium.launch_persistent_context(
            user_data_dir=str(Path("browser_data").resolve()),
            headless=True
        )
        page = await ctx.new_page()
        await page.goto("https://mingle2.com/mutual_match", wait_until="domcontentloaded")
        await asyncio.sleep(2)
        print("URL:", page.url)
        print("TITLE:", await page.title())

        info = await page.evaluate(r"""() => {
            const btns = Array.from(document.querySelectorAll('button, a.btn, input[type="button"], input[type="submit"], .like, .pass, .btn-match, .btn-like, [data-action]')).map(b => ({
                tag: b.tagName,
                text: b.innerText.trim(),
                className: b.className,
                id: b.id,
                action: b.getAttribute('data-action') || b.getAttribute('onclick') || b.getAttribute('data-url')
            }));
            const user = document.querySelector('.username, h1, h2, .name, .profile-name');
            return {
                user: user ? user.innerText.trim() : null,
                buttons: btns.slice(0, 15)
            };
        }""")
        print(json.dumps(info, indent=2))
        await page.screenshot(path="data/mutual_match_screen.png")
        await ctx.close()

if __name__ == "__main__":
    asyncio.run(inspect_mutual_match())
