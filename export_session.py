#!/usr/bin/env python3
"""
MingleBot Session Export Tool
Packages your authenticated browser session and database into a zip file
for fast transfer to your 24/7 cloud server.
"""

import os
import zipfile
from pathlib import Path

SKIP_DIRS = {"Cache", "Code Cache", "GPUCache", "DawnCache", "GrShaderCache", "Crashpad"}

def export_session():
    base_dir = Path(__file__).resolve().parent
    zip_path = base_dir / "minglebot_session.zip"
    
    print("Creating minglebot_session.zip...")
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
        # 1. Include database files and storage_state.json
        data_dir = base_dir / "data"
        if data_dir.exists():
            for f in data_dir.glob("*.db"):
                if f.is_file() and not f.name.endswith("-journal"):
                    arcname = f"data/{f.name}"
                    zf.write(f, arcname)
                    print(f"  [+] Added: {arcname}")
            storage_file = data_dir / "storage_state.json"
            if storage_file.exists():
                zf.write(storage_file, "data/storage_state.json")
                print("  [+] Added: data/storage_state.json (Cross-Platform Cookies)")

        # 2. Include browser_data (cookies, session data, skipping heavy caches)
        browser_dir = base_dir / "browser_data"
        if browser_dir.exists():
            count = 0
            for root, dirs, files in os.walk(browser_dir):
                # Modify dirs in-place to skip cache directories
                dirs[:] = [d for d in dirs if d not in SKIP_DIRS]
                for file in files:
                    if file.startswith("Singleton") or file.endswith(".tmp"):
                        continue
                    file_path = Path(root) / file
                    arcname = os.path.relpath(file_path, base_dir)
                    try:
                        zf.write(file_path, arcname)
                        count += 1
                    except Exception:
                        pass
            print(f"  [+] Added: browser_data/ ({count} essential session files)")

    size_mb = os.path.getsize(zip_path) / (1024 * 1024)
    print(f"\nSUCCESS! Session package created at: {zip_path.name}")
    print(f"File size: {size_mb:.2f} MB")
    print("\nTo deploy to your cloud server:")
    print("  1. Copy your project folder and 'minglebot_session.zip' to your VPS.")
    print("  2. Run: bash setup_vps.sh")
    print("  3. Your bot will run 24/7 with your active Mingle2 login!")

if __name__ == "__main__":
    export_session()
