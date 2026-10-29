import asyncio
import json
from pathlib import Path
from playwright.async_api import async_playwright
from src.bot_engine import setup_ad_blocking

async def probe_thread():
    async with async_playwright() as p:
        ctx = await p.chromium.launch_persistent_context(
            user_data_dir=str(Path("browser_data").resolve()),
            headless=True
        )
        await setup_ad_blocking(ctx)
        page = await ctx.new_page()
        await page.goto("https://mingle2.com/inbox/list", wait_until="domcontentloaded")

        first_conv = await page.evaluate("""() => {
            const a = document.querySelector('a[href*="/inbox/conversation/"]');
            return a ? a.href : null;
        }""")
        print("First conversation URL:", first_conv)
        if first_conv:
            await page.goto(first_conv, wait_until="domcontentloaded")
            thread_info = await page.evaluate("""() => {
                const header = document.querySelector('.username, h1, h2, .chat-header, .name');
                const messages = Array.from(document.querySelectorAll('.message, .chat-message, .msg, div[class*="message"], .chat-bubble, .speech-bubble')).slice(-6).map(m => m.innerText.replace(/\\s+/g, ' ').trim());
                const input = document.querySelector('textarea, input[type="text"][name*="body"], input[type="text"][name*="message"], #message_body, #chat_input, #reply_body');
                const sendBtn = document.querySelector('button[type="submit"], input[type="submit"], .send-btn, #send_message, .btn-send');
                const form = document.querySelector('form[action*="message"], form[action*="conversation"], form');
                return {
                    title: document.title,
                    url: window.location.href,
                    header: header ? header.innerText.trim() : null,
                    recentMessages: messages,
                    hasInput: !!input,
                    inputSelector: input ? (input.id ? '#' + input.id : input.name) : null,
                    formAction: form ? form.action : null
                };
            }""")
            print("THREAD INFO:", json.dumps(thread_info, indent=2))
        await ctx.close()

if __name__ == "__main__":
    asyncio.run(probe_thread())
