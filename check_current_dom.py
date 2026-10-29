import asyncio
import json
from pathlib import Path
from playwright.async_api import async_playwright

async def inspect():
    async with async_playwright() as p:
        context = await p.chromium.launch_persistent_context(
            user_data_dir=str(Path("browser_data").resolve()),
            headless=True
        )
        page = await context.new_page()
        await page.goto("https://mingle2.com/mutual_match/who_is_interested_in_me", wait_until="domcontentloaded")
        print("Page URL:", page.url)

        res = await page.evaluate("""() => {
            const allLinks = Array.from(document.querySelectorAll('a')).map(a => a.href);
            const loginLinks = allLinks.filter(h => h.includes('login') || h.includes('sign_in'));
            const logoutLinks = allLinks.filter(h => h.includes('logout') || h.includes('sign_out'));
            const userNav = document.querySelector('.user-menu, .profile-nav, .user-avatar, #nav-user, a[href*="/inbox"]');
            
            const testOldSignIn = document.querySelector('a[href*="/user/login"], a[href*="sign_in"], a[href*="login"], .sign-in-btn');
            const testOldLogout = document.querySelector('a[href*="logout"], a[href*="sign_out"]');
            
            return {
                url: window.location.href,
                loginLinks: loginLinks.slice(0, 10),
                logoutLinks: logoutLinks.slice(0, 10),
                hasUserNav: !!userNav,
                testOldSignInFound: !!testOldSignIn,
                testOldSignInHref: testOldSignIn ? testOldSignIn.href : null,
                testOldSignInHTML: testOldSignIn ? testOldSignIn.outerHTML : null,
                testOldLogoutFound: !!testOldLogout,
                testOldLogoutHref: testOldLogout ? testOldLogout.href : null
            };
        }""")
        print(json.dumps(res, indent=2))
        await context.close()

if __name__ == "__main__":
    asyncio.run(inspect())
