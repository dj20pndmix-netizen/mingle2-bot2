# Running MingleBot Independently on Your Phone

This guide shows you how to run and control your Mingle2 Bot directly from your Android phone as an independent standalone app.

---

## Architecture Overview

```
 ┌────────────────────────────────────────┐
 │   24/7 Cloud Server (VPS or Docker)    │
 │   • Automated Playwright Headless Bot  │
 │   • Auto Likers Scraper & Like-Back    │
 │   • SQLite Database & Background Tasks │
 └──────────────────┬─────────────────────┘
                    │ Real-Time WebSocket & API
                    ▼
 ┌────────────────────────────────────────┐
 │   Your Android Phone (Standalone App)  │
 │   • Full-Screen PWA (Add to Home)      │
 │   • Real-Time Queue & Stats Monitoring │
 │   • 1-Tap Trigger Scans & Custom Likes │
 └────────────────────────────────────────┘
```

---

## Option 1: Immediate Access from Your Android Phone (Same WiFi)

If your computer and your Android phone are on the same Wi-Fi network:

1. **Find your computer's local IP address**:
   - On Windows, open Command Prompt or PowerShell and type `ipconfig`.
   - Find your **IPv4 Address** (for example: `192.168.1.150`).
2. **Ensure MingleBot is running on `0.0.0.0`**:
   - The bot server is running on port `8000`.
3. **Open on your Android phone**:
   - Open Chrome on your phone and go to: `http://<YOUR_PC_IP>:8000` (e.g. `http://192.168.1.150:8000`).
4. **Install as an App**:
   - In Chrome, tap the **3 dots menu** (top right) ➔ tap **"Add to Home screen"** or **"Install app"**.
   - MingleBot will install with its custom app icon on your phone's home screen!

---

## Option 2: 24/7 Cloud Server Deployment (Recommended)

To run the bot 24/7 without needing your PC to stay powered on:

### Step 1: Export Your Authenticated Session
Run this command on your PC to package your active Mingle2 login session and database:
```bash
python export_session.py
```
This generates `minglebot_session.zip`.

### Step 2: Rent a Cloud VPS ($3 - $5/mo or Free)
Any Linux cloud server works:
- **Hetzner Cloud** (from ~€3/mo)
- **DigitalOcean Droplet** (from ~$4-6/mo)
- **Oracle Cloud Free Tier** (100% free always)
- **AWS EC2 / Lightsail**

Choose **Ubuntu 22.04 LTS** or **Ubuntu 24.04 LTS**.

### Step 3: Copy Your Project to the VPS
Upload your project files and `minglebot_session.zip` to the server:
```bash
scp -r "c:\mingle bot 1" user@your-server-ip:/home/user/minglebot
```

### Step 4: Run the 1-Click Setup Script
SSH into your server and run:
```bash
cd /home/user/minglebot
bash setup_vps.sh
```
The script will automatically:
- Install Docker & Docker Compose
- Unpack your active Mingle2 session
- Configure firewall rules
- Launch MingleBot in headless mode with automatic restart on boot!

### Step 5: Open on Your Phone
Visit `http://<YOUR_VPS_IP>:8000` on your Android phone, tap **"Install App"** / **"Add to Home screen"**, and control your bot 24/7 from anywhere in the world!
