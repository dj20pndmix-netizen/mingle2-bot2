"""
Mingle2 One-Click Login & Auto-Sync
Launches visible browser on your PC, waits for you to log in,
saves cookies, packages minglebot_session.zip, and pushes to Render!
"""
import asyncio
import os
import subprocess
import sys
from pathlib import Path

# Add project root to path
sys.path.insert(0, str(Path(__file__).resolve().parent))

from src.config import settings
from src.bot_engine import Mingle2BotEngine
from src.session_state import SessionState
from src.session_manager import export_session_archive

async def main():
    print("=" * 65)
    print("      Mingle2 One-Click Interactive Login & Cloud Sync        ")
    print("=" * 65)
    print("\n[1/3] Launching visible Chromium browser on your screen...")
    print("      Please log into Mingle2 in the browser window that opens.")
    print("      (You can enter your email, password, and solve any CAPTCHA)\n")

    engine = Mingle2BotEngine()
    try:
        res = await engine.open_login_browser()
        if res.get("status") == "error":
            print(f"[!] Error opening browser: {res.get('message')}")
            return

        print("[2/3] Waiting for you to log into Mingle2...")
        logged_in = await engine.wait_for_login(timeout=300)

        if not logged_in:
            print("\n[!] Login was not detected within 5 minutes or browser was closed.")
            print("    Please run this script again whenever you are ready.")
            return

        print("\n[+] SUCCESS: Logged in and authenticated successfully on Mingle2!")
        print("[+] Syncing your Mingle2 account profile...")
        try:
            profile = await engine.sync_account_profile()
            print(f"    Connected as: {profile.get('username')} (ID: {profile.get('user_id')})")
        except Exception as e:
            print(f"    Profile sync note: {e}")

        print("\n[3/3] Packaging session archive (minglebot_session.zip)...")
        zip_path = export_session_archive()
        print(f"[+] Saved session archive to: {zip_path}")

        # Auto-push to GitHub so Render deploys automatically
        print("\n[+] Pushing session to GitHub so Render automatically goes live...")
        try:
            subprocess.run(["git", "add", "minglebot_session.zip", "data/storage_state.json"], check=True)
            subprocess.run(["git", "commit", "-m", "chore(session): update active authenticated Mingle2 session"], check=False)
            push_res = subprocess.run(["git", "push", "origin", "main"], capture_output=True, text=True)
            if push_res.returncode == 0:
                print("\n" + "=" * 65)
                print(">>> 100% COMPLETE! Render is now deploying with your active login!")
                print(">>> Your bot will be ONLINE and CONNECTED on Render in 2 minutes.")
                print("=" * 65)
            else:
                print(f"\n[!] Note: Could not auto-push to git ({push_res.stderr.strip()}).")
                print("    You can upload 'minglebot_session.zip' directly in the Render dashboard,")
                print("    OR run: git push origin main")
        except Exception as git_err:
            print(f"[!] Git push helper note: {git_err}")
            print("    You can simply click 'Upload Session Zip' on your Render dashboard and choose minglebot_session.zip!")

    finally:
        await engine.stop_active_browser()

if __name__ == "__main__":
    asyncio.run(main())
