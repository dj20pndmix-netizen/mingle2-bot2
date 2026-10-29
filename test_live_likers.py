import asyncio
import json
from pathlib import Path
from playwright.async_api import async_playwright
import sys

BASE_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(BASE_DIR))

from src.bot_engine import setup_ad_blocking, strip_google_vignette

async def check_live_likers():
    print("[1/4] Launching persistent browser context...")
    async with async_playwright() as p:
        ctx = await p.chromium.launch_persistent_context(
            user_data_dir=str(Path("browser_data").resolve()),
            headless=True,
            args=["--disable-blink-features=AutomationControlled", "--no-sandbox"],
            ignore_default_args=["--enable-automation"],
        )
        await setup_ad_blocking(ctx)
        page = await ctx.new_page()

        print("[2/4] Navigating to https://mingle2.com/mutual_match/who_is_interested_in_me ...")
        await page.goto("https://mingle2.com/mutual_match/who_is_interested_in_me", wait_until="domcontentloaded", timeout=30000)
        await asyncio.sleep(2.5)

        url = strip_google_vignette(page.url)
        title = await page.title()
        print(f"      Current URL: {url}")
        print(f"      Page Title:  {title}")

        # Check if redirected to login
        if "/user/login" in url.lower() or "login" in url.lower() or "sign_in" in url.lower():
            print("[WARN] Redirected to login page! Session is currently logged out.")
            await page.screenshot(path="data/live_login_redirect.png")
            await ctx.close()
            return

        # Scroll down to load all infinite-scroll likers
        print("[3/4] Scrolling down to trigger dynamic loading of all cards...")
        for _ in range(5):
            await page.mouse.wheel(0, 1200)
            await asyncio.sleep(1.0)

        # Scrape page statistics, headings, and all cards
        page_info = await page.evaluate(r"""() => {
            const heading = document.querySelector('h1, h2, .page-title, .title, .section-title');
            const headingText = heading ? heading.innerText.trim() : '';

            // Look for any text mentioning interested count, e.g., "14 people are interested in you"
            const bodyText = document.body.innerText;
            const countMatch = bodyText.match(/(\d+)\s*(?:people|users|members|mutual|likers?)\s*(?:are\s*)?(?:interested|like)/i) ||
                               bodyText.match(/(?:who['’]s\s*interested|likers?)\s*\(?(\d+)\)?/i);

            // Find all user profile cards or links
            const cards = [];
            const seen = new Set();
            const elements = document.querySelectorAll(
                '.match_card, .user_card, .interested_user, .user-item, .profile-item, ' +
                'div[id^="user_"], div[data-user-id], .card, li.user, .member-card'
            );

            elements.forEach(el => {
                const link = el.querySelector('a[href*="/user/view/"], a[href*="/profile/"], a[href*="/user/"]');
                if (!link) return;
                const m = link.href.match(/(?:user\/view|profile|user)\/([a-zA-Z0-9_-]+)/i);
                const uid = el.getAttribute('data-user-id') || (m ? m[1] : null);
                if (!uid || seen.has(uid)) return;
                seen.add(uid);

                const nameEl = el.querySelector('.username, .name, strong, h3, h4, h2') || link;
                const name = nameEl ? nameEl.innerText.trim() : uid;

                const img = el.querySelector('img');
                const avatar = img ? (img.getAttribute('data-src') || img.src) : '';

                const detailsEl = el.querySelector('.user_details, .details, .meta, .age-location, p');
                const details = detailsEl ? detailsEl.innerText.replace(/\s+/g, ' ').trim() : '';

                cards.push({ user_id: uid, username: name, details: details, avatar: avatar, url: link.href });
            });

            // If card containers weren't found, find direct links
            if (cards.length === 0) {
                const directLinks = document.querySelectorAll('a[href*="/user/view/"], a[href*="/user/profile/"]');
                directLinks.forEach(l => {
                    const m = l.href.match(/(?:user\/view|profile|user)\/([a-zA-Z0-9_-]+)/i);
                    if (!m) return;
                    const uid = m[1];
                    if (seen.has(uid)) return;
                    seen.add(uid);
                    cards.push({
                        user_id: uid,
                        username: l.innerText.trim() || uid,
                        details: '',
                        avatar: '',
                        url: l.href
                    });
                });
            }

            return {
                headingText: headingText,
                detectedCountInText: countMatch ? countMatch[1] : null,
                totalCardsFound: cards.length,
                sampleCards: cards.slice(0, 10)
            };
        }""")

        print("[4/4] Live Page Inspection Results:")
        print(json.dumps(page_info, indent=2))

        await page.screenshot(path="data/live_likers_inspected.png", full_page=True)
        print("      Screenshot saved to: data/live_likers_inspected.png")

        await ctx.close()

if __name__ == "__main__":
    asyncio.run(check_live_likers())
