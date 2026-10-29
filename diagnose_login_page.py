import asyncio
import json
from playwright.async_api import async_playwright

async def inspect():
    async with async_playwright() as p:
        browser = await p.chromium.launch(
            headless=True,
            args=[
                "--disable-blink-features=AutomationControlled",
                "--no-sandbox"
            ],
            ignore_default_args=["--enable-automation"]
        )
        context = await browser.new_context(
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36"
        )
        page = await context.new_page()
        page.on("console", lambda msg: print(f"PAGE LOG [{msg.type}]: {msg.text}"))
        page.on("pageerror", lambda err: print(f"PAGE ERROR: {err}"))

        await page.goto("https://mingle2.com/user/login", wait_until="networkidle")

        info = await page.evaluate("""() => {
            const btn = document.querySelector('#login-form input[type="submit"], #login-form button[type="submit"], #login-form .btn');
            const hasRecaptcha = !!document.querySelector('.g-recaptcha, iframe[src*="recaptcha"], iframe[src*="challenges.cloudflare"], .cf-turnstile');
            const scripts = Array.from(document.querySelectorAll('script[src]')).map(s => s.src);
            const form = document.querySelector('#login-form');
            return {
                submitBtnText: btn ? (btn.value || btn.innerText) : null,
                submitBtnDisabled: btn ? btn.disabled : null,
                formAction: form ? form.action : null,
                hasRecaptcha,
                scripts
            };
        }""")
        print(json.dumps(info, indent=2))
        await browser.close()

if __name__ == "__main__":
    asyncio.run(inspect())
