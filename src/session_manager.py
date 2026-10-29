"""
Mingle2 Bot Session Manager
Handles session archiving, extraction, auto-restoration, and verification
for seamless local and cloud (Render/VPS) deployments.
"""

import os
import shutil
import zipfile
import logging
from pathlib import Path
from typing import Any, Dict, Optional

logger = logging.getLogger("mingle2.session")

BASE_DIR = Path(__file__).resolve().parent.parent
SESSION_ZIP = BASE_DIR / "minglebot_session.zip"
BROWSER_DATA = BASE_DIR / "browser_data"
SESSION_STAMP = BROWSER_DATA / ".session_stamp"

SKIP_DIRS = {"Cache", "Code Cache", "GPUCache", "DawnCache", "GrShaderCache", "Crashpad"}


def restore_session_archive(force: bool = False) -> bool:
    """
    Extract minglebot_session.zip if present and browser_data is empty or archive is newer.
    Returns True if archive was extracted.
    """
    if not SESSION_ZIP.exists():
        logger.info("No session archive (minglebot_session.zip) found to restore.")
        return False

    zip_mtime = SESSION_ZIP.stat().st_mtime
    should_extract = force

    if not should_extract:
        has_files = BROWSER_DATA.exists() and any(BROWSER_DATA.iterdir())
        if not has_files:
            should_extract = True
        elif SESSION_STAMP.exists():
            try:
                last_mtime = float(SESSION_STAMP.read_text().strip())
                if zip_mtime > last_mtime + 1.0:
                    should_extract = True
            except Exception:
                should_extract = True
        else:
            should_extract = True

    if should_extract:
        logger.info("Unpacking session archive (minglebot_session.zip)...")
        try:
            with zipfile.ZipFile(SESSION_ZIP, "r") as zf:
                zf.extractall(BASE_DIR)
            BROWSER_DATA.mkdir(parents=True, exist_ok=True)
            SESSION_STAMP.write_text(str(zip_mtime))
            logger.info("Session restored successfully from minglebot_session.zip.")
            return True
        except Exception as e:
            logger.warning(f"Failed to unpack session archive: {e}")
            return False

    return False


def export_session_archive() -> Path:
    """
    Package current authenticated browser session and SQLite databases into minglebot_session.zip.
    """
    logger.info("Creating minglebot_session.zip archive...")
    with zipfile.ZipFile(SESSION_ZIP, "w", zipfile.ZIP_DEFLATED) as zf:
        # 1. Include database files and cross-platform storage state
        data_dir = BASE_DIR / "data"
        if data_dir.exists():
            for f in data_dir.glob("*.db"):
                if f.is_file() and not f.name.endswith("-journal"):
                    arcname = f"data/{f.name}"
                    zf.write(f, arcname)

            storage_state = data_dir / "storage_state.json"
            if storage_state.exists():
                zf.write(storage_state, "data/storage_state.json")
                logger.info("  [+] Added cross-platform data/storage_state.json")

        # 2. Include browser_data (cookies, storage, preferences)
        if BROWSER_DATA.exists():
            for root, dirs, files in os.walk(BROWSER_DATA):
                dirs[:] = [d for d in dirs if d not in SKIP_DIRS]
                for file in files:
                    if file.startswith("Singleton") or file.endswith(".tmp"):
                        continue
                    file_path = Path(root) / file
                    arcname = os.path.relpath(file_path, BASE_DIR)
                    try:
                        zf.write(file_path, arcname)
                    except Exception:
                        pass

    # Update stamp
    zip_mtime = SESSION_ZIP.stat().st_mtime
    BROWSER_DATA.mkdir(parents=True, exist_ok=True)
    SESSION_STAMP.write_text(str(zip_mtime))

    size_mb = os.path.getsize(SESSION_ZIP) / (1024 * 1024)
    logger.info(f"Session package successfully created: {SESSION_ZIP.name} ({size_mb:.2f} MB)")
    return SESSION_ZIP


def get_session_info() -> Dict[str, Any]:
    """Return status metadata about local browser_data and session archive."""
    has_zip = SESSION_ZIP.exists()
    has_browser_data = BROWSER_DATA.exists() and any(BROWSER_DATA.iterdir())
    zip_size_mb = (os.path.getsize(SESSION_ZIP) / (1024 * 1024)) if has_zip else 0.0

    return {
        "has_archive": has_zip,
        "archive_size_mb": round(zip_size_mb, 2),
        "archive_mtime": SESSION_ZIP.stat().st_mtime if has_zip else None,
        "has_browser_data": has_browser_data,
    }
