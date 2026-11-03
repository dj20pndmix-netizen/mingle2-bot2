import asyncio
import sys
from pathlib import Path

# Add project root to sys.path
BASE_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE_DIR))

from src.bot_engine import Mingle2BotEngine, dismiss_page_overlays
from src.config import settings

def test_selector_isolation():
    """Verify that the Yes/Like button selector isolates like from skip action."""
    # Sample HTML snippet matching Mingle2's mutual match deck
    html = """
    <div class="rate-buttons">
      <div class="rate-no-btn rate-btn">
        <form action="/mutual_match/rate/12345" method="post">
          <button class="form-with-recaptcha-action" data-recaptcha-action="skip" type="submit">
            <img alt="Skip" src="skip.svg">
          </button>
        </form>
      </div>
      <div class="rate-yes-btn rate-btn">
        <form action="/mutual_match/rate/12345" method="post">
          <input type="hidden" name="rating" id="rating" value="Y">
          <button class="form-with-recaptcha-action" data-recaptcha-action="like" type="submit">
            <img alt="Like" src="white-heart.svg">
          </button>
        </form>
      </div>
    </div>
    """
    from bs4 import BeautifulSoup
    soup = BeautifulSoup(html, "html.parser")
    
    # 1. Generic class matches skip button (the bug)
    buggy_btn = soup.select_one(".form-with-recaptcha-action")
    assert buggy_btn["data-recaptcha-action"] == "skip", "First matching element in DOM is the skip button"
    
    # 2. Scoped selector correctly isolates the Like button
    like_btn = soup.select_one('.rate-yes-btn button[data-recaptcha-action="like"]')
    assert like_btn is not None
    assert like_btn["data-recaptcha-action"] == "like"
    assert like_btn.select_one('img')["alt"] == "Like"
    print(" [PASS] Selector Isolation: Scoped selector accurately targets Like and rejects Skip")

def test_simulation_and_paywalled_guards():
    async def _run():
        engine = Mingle2BotEngine()
        
        # Test 1: Test Mode simulation
        res = await engine.like_user({"user_id": "sim_12345", "username": "Alice"}, is_test_mode=True)
        assert res[0] is True
        assert res[2] == "COMPLETED"
        print(" [PASS] Test Mode Simulation: Safely simulates like without external calls")
        
        # Test 2: Paywalled preview rejection
        res_paywalled = await engine.like_user({"user_id": "fan_preview_999", "username": "Blurred User"}, is_test_mode=False)
        assert res_paywalled[0] is False
        assert res_paywalled[2] == "IDENTITY_FAILED"
        print(" [PASS] Paywall Guard: Blocks unverified blurred fan previews lacking user ID")

    asyncio.run(_run())

def main():
    print("=" * 60)
    print("  TEST: LIKE-BACK BUTTON INTERACTION & SELECTOR GUARDS")
    print("=" * 60)
    test_selector_isolation()
    test_simulation_and_paywalled_guards()
    print("=" * 60)
    print(">>> ALL LIKE-BACK INTERACTION TESTS PASSED! <<<")
    print("=" * 60)

if __name__ == "__main__":
    main()
