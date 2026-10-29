# Mingle2 Command Center & Multi-Task Automation Suite

A full-sync, multi-task Command Center and autonomous suite for Mingle2. The application provides two-way synchronization and autonomous task dispatching across 7 key Mingle2 modules + a Task Dispatcher, wrapped in a high-aesthetic cyber-chic glassmorphic Web UI.

---

## Architectural Modules & Capabilities

| Module | Mingle2 Endpoint | Purpose & Capabilities |
| :--- | :--- | :--- |
| **Dashboard** | Overview | Real-time metric counters, quick task launcher, active background tasks, system health. |
| **Account** | `/user/my_profile` | Live profile sync: username, avatar, biography, age, location, photos gallery, profile stats. |
| **Mailbox / Inbox** | `/inbox/list`, `/inbox/conversation/:id` | Two-pane messaging UI: read conversation threads, view chat histories, send live replies. |
| **Matches & Likes** | `/mutual_match/who_is_interested_in_me` | Scheduled 5-minute delayed like-backs with jitter, human mouse curve, instant like-now override. |
| **Search** | `/search` | Basic & Advanced search by gender, age bracket, country, keywords; 1-click Like and Message. |
| **Who's Online** | `/online`, `/search` | Live active member feed with pulsating activity badges and instant interaction controls. |
| **Task Dispatcher** | Background Worker | Autonomous background tasks (`AUTO_LIKE_BACK`, `LIKE_ONLINE_USERS`, `BULK_SEARCH_AND_LIKE`, `AUTO_CHECK_INBOX`, `SYNC_FULL_ACCOUNT`) with animated progress bars & percentages. |
| **Live Console** | WebSocket feed | Terminal-style real-time streaming activity logs. |

---

## Directory Structure

```text
mingle2-bot/
├── data/
│   ├── mingle_bot.db        # SQLite database (account, threads, messages, tasks, search, queue)
│   └── test_screenshots/    # UI verification screenshots
├── browser_data/            # Persistent Chromium profile & authenticated session cookies
├── src/
│   ├── __init__.py
│   ├── config.py            # App settings (Pydantic / path configurations)
│   ├── database.py          # SQLite schema & CRUD methods for all 7 modules
│   ├── bot_engine.py        # Playwright scraper, anti-detection stealth, & action executor
│   ├── scheduler.py         # Task Dispatcher worker loop + Like-back countdown scheduler
│   └── server.py            # FastAPI backend, REST API endpoints, & WebSocket broadcaster
├── static/
│   ├── index.html           # Full 8-tab Command Center Single Page Application
│   ├── app.js               # Reactive WebSocket client, chat manager, & task tracker
│   └── style.css            # Dark glassmorphic styles, custom scrollbars, & progress stripes
├── tests/
│   ├── test_ui.py           # Playwright UI verification test
│   └── test_full_suite.py   # Full end-to-end multi-module test suite
├── login.py                 # Standalone manual login helper script
├── login.bat                # 1-click login launcher for Windows
├── run.py                   # Main entry point to launch the Command Center
├── run.bat                  # 1-click runner for Windows
├── requirements.txt         # Pinned Python dependencies
└── README.md
```

---

## Quickstart & Installation

### 1. Prerequisites
- Python 3.10+ installed.
- Windows, macOS, or Linux.

### 2. Setup Virtual Environment
```bash
# Windows
python -m venv .venv
.venv\Scripts\activate

# Install dependencies
pip install -r requirements.txt

# Install Playwright browser binaries
playwright install chromium
```

### 3. Launch the Command Center
Double-click `run.bat` or run:
```bash
python run.py
```

The Web Dashboard will launch at:
👉 **`http://127.0.0.1:8000`**

---

## Authentication & Session Management

1. In the Web UI header, click **"Open Browser for Login"** (or run `login.bat`).
2. A visible Chromium window will open to `https://mingle2.com/user/login`.
3. Sign into your Mingle2 account and solve any CAPTCHA challenge.
4. Once logged in, close the browser window or leave it. Your session cookies and tokens are permanently stored in `./browser_data`.
5. Click **"Start Bot"** or **"Sync All"** to begin automated operation!

---

## Automated Verification & Testing

To run the complete automated test suite across all modules:
```bash
.venv\Scripts\python tests/test_full_suite.py
```
This suite automatically tests:
- Server startup and WebSocket connection.
- Navigation through all 8 module tabs.
- Queuing and tracking background automation tasks.
- Incoming like simulation and dynamic 5-minute countdowns.
- Instant Like-Now actions.
- Mailbox chat input and thread rendering.
- Full-page verification screenshot saved to `data/command_center_verified.png`.
