#!/usr/bin/env bash
# ==============================================================================
# MingleBot 24/7 Cloud VPS Automated Setup Script (Ubuntu / Debian)
# ==============================================================================
set -e

echo "=== [1/4] Updating packages and installing system tools ==="
sudo apt-get update -y
sudo apt-get install -y curl git ufw unzip

echo "=== [2/4] Installing Docker and Docker Compose ==="
if ! command -v docker &> /dev/null; then
    curl -fsSL https://get.docker.com -o get-docker.sh
    sudo sh get-docker.sh
    sudo usermod -aG docker "$USER"
    rm get-docker.sh
    echo "Docker installed successfully."
fi

echo "=== [3/4] Configuring Firewall (UFW) ==="
sudo ufw allow 22/tcp || true
sudo ufw allow 80/tcp || true
sudo ufw allow 443/tcp || true
sudo ufw allow 8000/tcp || true
sudo ufw --force enable || true

echo "=== [4/4] Building and launching MingleBot container ==="
if [ -f "minglebot_session.zip" ]; then
    echo "Found session archive: extracting data and browser session..."
    unzip -o minglebot_session.zip
fi

docker compose down || true
docker compose up -d --build

SERVER_IP=$(curl -s https://ifconfig.me || hostname -I | awk '{print $1}')
echo ""
echo "=================================================================="
echo "  MingleBot is now running 24/7 in the cloud!"
echo "  Access from your phone: http://${SERVER_IP}:8000"
echo "=================================================================="
echo "Tip: Open this URL in Chrome on your Android phone, tap the 3 dots,"
echo "and select 'Install App' or 'Add to Home Screen'!"
echo "=================================================================="
