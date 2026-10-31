#!/usr/bin/env python3
"""
Mingle2 Like-Back Automation Bot & Web Dashboard
Single Entry Point Script
"""

import logging
import os
import sys
import webbrowser
from pathlib import Path

# Add project root to sys.path
BASE_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(BASE_DIR))

# Auto-detect and switch to .venv interpreter if running outside virtual environment
venv_python = (BASE_DIR / ".venv" / "Scripts" / "python.exe") if os.name == "nt" else (BASE_DIR / ".venv" / "bin" / "python")
if venv_python.exists() and Path(sys.executable).resolve() != venv_python.resolve():
    import subprocess
    try:
        sys.exit(subprocess.run([str(venv_python)] + sys.argv).returncode)
    except KeyboardInterrupt:
        sys.exit(0)

try:
    import uvicorn
except ImportError:
    print(f"Error: Missing dependencies. Please run: {venv_python} -m pip install -r requirements.txt")
    sys.exit(1)

from src.config import settings
from src.session_manager import restore_session_archive


def setup_logging():
    settings.ensure_directories()
    restore_session_archive()
    log_format = "[%(asctime)s] [%(levelname)s] [%(name)s]: %(message)s"

    logging.basicConfig(
        level=logging.INFO,
        format=log_format,
        handlers=[
            logging.FileHandler(settings.log_path, encoding="utf-8"),
            logging.StreamHandler(sys.stdout),
        ],
    )


def print_banner():
    display_host = "localhost" if settings.host in ("0.0.0.0", "::", "") else settings.host
    banner = f"""
========================================================================
             Mingle2 "Like-Back" Automation Bot & Web UI               
========================================================================
 [•] Web Dashboard:    http://{display_host}:{settings.port}
 [•] Local Loopback:   http://127.0.0.1:{settings.port}
 [•] SQLite Database:  {settings.db_path}
 [•] Session Storage:  {settings.browser_data_dir}
 [•] Like-Back Delay:  {settings.like_delay_minutes} minutes (with randomized jitter)
 [•] Headless Mode:    {settings.headless_mode}
========================================================================
Tip: On first launch, click 'Sign In to Mingle2' in the Dashboard
     to authenticate and solve Cloudflare/CAPTCHAs securely.
========================================================================
"""
    print(banner)


def main():
    setup_logging()
    print_banner()

    host = os.getenv("HOST", settings.host)
    port = int(os.getenv("PORT", settings.port))
    display_host = "localhost" if host in ("0.0.0.0", "::", "") else host

    # Auto-launch of dashboard in default browser (only in desktop non-headless mode)
    try:
        if not settings.headless_mode and os.environ.get("OPEN_BROWSER", "1") == "1":
            dashboard_url = f"http://{display_host}:{port}"
            webbrowser.open(dashboard_url)
    except Exception:
        pass

    # Start FastAPI server on dynamic Render/host port
    logging.info(f"Binding MingleBot web server to {host}:{port} (Render PORT={os.getenv('PORT')})")

    uvicorn.run(
        "src.server:app",
        host=host,
        port=port,
        reload=False,
        log_level="info",
    )


if __name__ == "__main__":
    main()
