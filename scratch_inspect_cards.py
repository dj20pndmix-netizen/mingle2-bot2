import asyncio
import json
from pathlib import Path
from playwright.async_api import async_playwright

async def inspect():
    async with async_playwright() as p:
        ctx = await p.chromium.launch_persistent_context(
            user_data_dir=str(Path("browser_data").resolve()),
            headless=True
        )
        page = await ctx.new_page()
        await page.goto("https://mingle2.com/mutual_match/who_is_interested_in_me", wait_until="domcontentloaded")
        await asyncio.sleep(2)

        data = await page.evaluate(r"""() => {
            // Find Match nav badge count (e.g. 87)
            let matchCount = null;
            const navLinks = Array.from(document.querySelectorAll('a'));
            for (const a of navLinks) {
                if (a.href && a.href.includes('mutual_match')) {
                    const m = a.innerText.match(/(\d+)/);
                    if (m) matchCount = parseInt(m[1]);
                }
            }

            // Inspect the cards container
            const allElements = Array.from(document.querySelectorAll('*'));
            const potentialCards = [];

            // Look for elements with age/gender text e.g. "Woman, age" or "Man, age"
            document.querySelectorAll('div, li, a, section').forEach(el => {
                const text = el.innerText || '';
                if ((text.includes('Woman,') || text.includes('Man,')) && text.length < 250) {
                    const img = el.querySelector('img');
                    const link = el.querySelector('a') || (el.tagName === 'A' ? el : null);
                    potentialCards.push({
                        tag: el.tagName,
                        className: el.className,
                        id: el.id,
                        href: link ? link.href : null,
                        imgSrc: img ? img.src : null,
                        text: text.replace(/\s+/g, ' ').trim()
                    });
                }
            });

            // Also check pagination
            const pagination = document.querySelector('.pagination, ul.pagination, .pager');
            const pageLinks = pagination ? Array.from(pagination.querySelectorAll('a, span')).map(x => x.innerText.trim()) : [];

            return {
                matchCountFromNav: matchCount,
                cardMatchesFound: potentialCards.length,
                sampleCards: potentialCards.slice(0, 10),
                pagination: pageLinks
            };
        }""")

        print(json.dumps(data, indent=2))
        await ctx.close()

if __name__ == "__main__":
    asyncio.run(inspect())
