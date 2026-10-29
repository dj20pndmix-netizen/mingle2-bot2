import asyncio
from pathlib import Path
from playwright.async_api import async_playwright

async def count_all():
    async with async_playwright() as p:
        ctx = await p.chromium.launch_persistent_context(
            user_data_dir=str(Path("browser_data").resolve()),
            headless=True
        )
        page = await ctx.new_page()
        total_items = []
        page_num = 1
        while True:
            url = f"https://mingle2.com/mutual_match/who_is_interested_in_me?page={page_num}"
            await page.goto(url, wait_until="domcontentloaded")
            await asyncio.sleep(1.5)
            cards = await page.evaluate(r"""() => {
                const items = document.querySelectorAll('.user-gallery-item.fan-item');
                return Array.from(items).map(c => {
                    const id = c.getAttribute('data-rating-id') || c.getAttribute('data-button-id');
                    const nameEl = c.querySelector('.display-name');
                    const name = nameEl ? nameEl.innerText.trim() : 'Unknown';
                    const ga = c.querySelector('.gender-and-age') ? c.querySelector('.gender-and-age').innerText.trim() : '';
                    const loc = c.querySelector('.location') ? c.querySelector('.location').innerText.trim() : '';
                    const date = c.querySelector('.date') ? c.querySelector('.date').innerText.trim() : '';
                    const img = c.querySelector('img') ? c.querySelector('img').src : '';
                    return {
                        id: id,
                        name: name,
                        age_location: ga + (loc ? ' • ' + loc : ''),
                        date: date,
                        img: img
                    };
                });
            }""")

            if not cards:
                print(f"Page {page_num}: No cards returned.")
                break

            print(f"Page {page_num}: Found {len(cards)} likers")
            total_items.extend(cards)

            # Check if next page exists
            has_next = await page.evaluate(r"""() => {
                const nextLink = document.querySelector('.pagination .next_page:not(.disabled), a[rel="next"], a.next');
                return !!nextLink;
            }""")

            if not has_next or page_num >= 10:
                break
            page_num += 1

        print(f"TOTAL REAL LIKERS ACROSS ALL PAGES: {len(total_items)}")
        for i, item in enumerate(total_items[:10]):
            print(f"  [{i+1}] {item['name']} ({item['age_location']}) - ID: {item['id']}")

        await ctx.close()

if __name__ == "__main__":
    asyncio.run(count_all())
