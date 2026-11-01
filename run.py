#!/usr/bin/env python3
"""
Mingle2 Like-Back Automation Bot & Web Dashboard
Single Entry Point Script
"""

import logging
import os
import socket
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


def is_port_in_use(port: int, host: str = "0.0.0.0") -> bool:
    """Check if a given port is currently bound or occupied."""
    hosts_to_test = [host]
    if host in ("0.0.0.0", "::", ""):
        hosts_to_test = ["0.0.0.0", "127.0.0.1"]
    for h in hosts_to_test:
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
                s.bind((h, port))
        except OSError:
            return True
    return False


def find_available_port(start_port: int, host: str = "0.0.0.0", max_attempts: int = 50) -> int:
    """Find the next available port starting from start_port."""
    for candidate in range(start_port, start_port + max_attempts):
        if not is_port_in_use(candidate, host):
            return candidate
    return start_port


def get_occupying_process_info(port: int) -> str:
    """Attempt to identify the PID holding the port for helpful diagnostics."""
    if os.name == "nt":
        try:
            import subprocess
            cmd = f'powershell -NoProfile -ExecutionPolicy Bypass -Command "$c = Get-NetTCPConnection -LocalPort {port} -ErrorAction SilentlyContinue; if ($c) {{ ($c | Select-Object -ExpandProperty OwningProcess -Unique) -join \', \' }}"'
            pids = subprocess.check_output(cmd, shell=True, text=True, stderr=subprocess.DEVNULL).strip()
            if pids:
                return f"PID {pids}"
        except Exception:
            pass
    return "Unknown process"


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

    host = os.getenv("HOST", settings.host)
    raw_port = int(os.getenv("PORT", settings.port))
    port = raw_port

    if is_port_in_use(raw_port, host):
        occupant = get_occupying_process_info(raw_port)
        if "PORT" in os.environ:
            logging.error(
                f"[WinError 10048] Port {raw_port} is already in use by {occupant}. "
                f"Please free port {raw_port} (run 'free_port_8000.bat' or terminate {occupant}) before restarting."
            )
            sys.exit(1)
        else:
            free_port = find_available_port(raw_port + 1, host)
            logging.warning(
                f"Port {raw_port} is already in use by {occupant}! "
                f"Automatically switching to available port {free_port} to prevent conflict. "
                f"(To free port {raw_port}, run 'free_port_8000.bat' or close the other process)."
            )
            port = free_port

    settings.port = port
    print_banner()

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

    try:
        uvicorn.run(
            "src.server:app",
            host=host,
            port=port,
            reload=False,
            log_level="info",
        )
    except OSError as e:
        if "10048" in str(e):
            logging.error(
                f"Port {port} conflict error: {e}. "
                f"Run 'free_port_8000.bat' or terminate the process holding the port."
            )
        else:
            raise


if __name__ == "__main__":
    main()
