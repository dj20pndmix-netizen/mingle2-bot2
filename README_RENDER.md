# Deploying MingleBot to Render.com (24/7 Cloud + Mobile Access)

Deploying MingleBot to **[Render.com](https://render.com/)** gives you a **live HTTPS URL** (e.g. `https://minglebot.onrender.com`) running 24/7 in the cloud. You can open and install this directly onto your Android phone as a native app.

---

## Step 1: Ensure Session Is Exported

Make sure `minglebot_session.zip` is created so Render starts up already logged into Mingle2:
```bash
python export_session.py
```
*(This is already created for you in the project root!)*

---

## Step 2: Push Your Project to GitHub / GitLab

1. Create a **Private Repository** on [GitHub](https://github.com/new) (e.g. `mingle-bot`).
2. In your project directory, push the code:
   ```bash
   git init
   git add .
   git commit -m "MingleBot Cloud & Mobile Release"
   git branch -M main
   git remote add origin https://github.com/<YOUR_USERNAME>/mingle-bot.git
   git push -u origin main
   ```

---

## Step 3: Create Service on Render.com

1. Go to **[dashboard.render.com](https://dashboard.render.com/)** and sign in.
2. Click **New +** ➔ Select **Web Service**.
3. Choose **Build and deploy from a Git repository** ➔ Select your `mingle-bot` repo.
4. Configure the service:
   - **Name**: `minglebot`
   - **Region**: Any (e.g. Oregon, Frankfurt)
   - **Runtime**: **Docker** (Render automatically detects your [`Dockerfile`](file:///c:/mingle%20bot%201/Dockerfile))
   - **Instance Type**:
     - *Free* (Spins down after 15 min of inactivity)
     - *Starter* ($7/mo — Recommended for 24/7 continuous autonomous automation without sleep)
5. Under **Environment Variables**, add:
   - `HEADLESS_MODE` = `true`
   - `PORT` = `8000`
   - `HOST` = `0.0.0.0`
6. Click **Create Web Service**.

---

## Step 4: Access and Install on Your Android Phone

1. Once Render finishes building (usually 2–3 minutes), you will see your live HTTPS URL:
   **`https://minglebot-xxxx.onrender.com`**
2. On your Android phone, open this URL in **Google Chrome**.
3. Tap the **3 vertical dots menu** (top right) ➔ tap **"Install App"** (or **"Add to Home screen"**).
4. **Done!** The MingleBot icon is now installed on your phone's home screen.
   - It runs completely independently in the cloud 24/7 even when your phone is turned off.
   - You can review mutual matches, trigger scans, and check live logs anytime from your phone.
