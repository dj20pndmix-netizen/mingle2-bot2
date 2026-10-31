import asyncio
import sys
from pathlib import Path

# Add project root to path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.bot_engine import Mingle2BotEngine
from src.session_state import SessionState

async def test_full_engine():
    print("=" * 60)
    print("  TEST 1: MINGLE2 BOT ENGINE & LIVE SESSION VERIFICATION")
    print("=" * 60)
    
    print("[1] Initializing Mingle2BotEngine...")
    engine = Mingle2BotEngine()
    
    print("[2] Running verify_mingle2_session()...")
    is_valid = await engine.verify_mingle2_session()
    print(f"    -> Verification result: {is_valid}")
    print(f"    -> Session State: {engine.session_state.value}")
    assert is_valid is True, "Expected valid session from storage_state.json"
    
    print("[3] Querying current account state...")
    state = await engine.get_current_account_state()
    print(f"    -> Connection Status: {state.get('connection_status')}")
    print(f"    -> Is Authenticated: {state.get('is_authenticated')}")
    assert state.get("is_authenticated") is True, "Expected authenticated state"
    
    print("[4] Testing stop_active_browser()...")
    await engine.stop_active_browser()
    print("    -> Browser context closed cleanly.")
    print("\n>>> ALL ENGINE TESTS PASSED SUCCESSFULLY! <<<\n")

if __name__ == "__main__":
    asyncio.run(test_full_engine())
