#!/bin/bash
set -e

# ─── Telegram Bot Installer ────────────────────────────────────────────
# Usage: bash install.sh [bot_token] [admin_id] [port] [directory]
#
# If arguments are omitted, the script will prompt for them interactively.

echo "============================================"
echo "       Telegram Bot - Installation"
echo "============================================"
echo ""

# ─── Get bot token ──────────────────────────────────────────────────────
if [ -n "$1" ]; then
    BOT_TOKEN="$1"
else
    read -p "Enter bot token (from @BotFather): " BOT_TOKEN
    while [ -z "$BOT_TOKEN" ]; do
        echo "Token cannot be empty."
        read -p "Enter bot token (from @BotFather): " BOT_TOKEN
    done
fi

# ─── Get admin ID ────────────────────────────────────────────────────────
if [ -n "$2" ]; then
    ADMIN_ID="$2"
else
    read -p "Enter admin numeric ID (from @userinfobot): " ADMIN_ID
    while [ -z "$ADMIN_ID" ]; do
        echo "Admin ID cannot be empty."
        read -p "Enter admin numeric ID (from @userinfobot): " ADMIN_ID
    done
fi

HTTP_PORT="${3:-8585}"
BOT_DIR="${4:-$(pwd)}"
ADMIN_IDS="[$ADMIN_ID]"

echo ""
echo "============================================"
echo "  Configuration"
echo "============================================"
echo "  Bot Token:    ${BOT_TOKEN:0:20}..."
echo "  Admin ID:     $ADMIN_ID"
echo "  HTTP Port:    $HTTP_PORT"
echo "  Install Dir:  $BOT_DIR"
echo "============================================"
echo ""

# ─── 1. System packages ─────────────────────────────────────────────────
echo "[1/7] Installing system packages..."
sudo apt update -qq
sudo apt install -y -qq python3 python3-pip python3-venv ffmpeg git curl wget

# ─── 2. Python virtual environment ──────────────────────────────────────
echo "[2/7] Creating Python virtual environment..."
cd "$BOT_DIR"
python3 -m venv venv
source venv/bin/activate

# ─── 3. Install Python packages ─────────────────────────────────────────
echo "[3/7] Installing Python packages..."
pip install -q --upgrade pip
pip install -q python-telegram-bot==22.8 yt-dlp instaloader cloudscraper curl_cffi beautifulsoup4 lxml Pillow qrcode[pil] rembg requests jdatetime hijridate

# ─── 4. Create directories ──────────────────────────────────────────────
echo "[4/7] Creating required directories..."
cd "$BOT_DIR"
mkdir -p data downloads/admin_dl downloads/qrcodes downloads/photos downloads/instagram downloads/videos

# ─── 5. Update bot.py with token ────────────────────────────────────────
echo "[5/7] Setting bot token in bot.py..."
if [ -f bot.py ]; then
    sed -i "s/BOT_TOKEN = \".*\"/BOT_TOKEN = \"$BOT_TOKEN\"/" bot.py
    echo "  Done."
else
    echo "  WARNING: bot.py not found!"
    exit 1
fi

# ─── 6. Create config files in data/ ────────────────────────────────────
echo "[6/7] Creating config files in data/..."

cat > data/config.json <<EOF
{
  "http_port": $HTTP_PORT
}
EOF

cat > data/admin.json <<EOF
{
  "admins": $ADMIN_IDS,
  "banned": []
}
EOF

cat > data/users.json <<EOF
{"users": []}
EOF

cat > data/limits.json <<EOF
{}
EOF

cat > data/short_urls.json <<EOF
{}
EOF

cat > data/instagram_login.json <<EOF
{"username": "", "password": ""}
EOF

echo "  Done."

# ─── 7. Create systemd service ──────────────────────────────────────────
echo "[7/7] Creating systemd service..."
SERVICE_FILE="/etc/systemd/system/telegram-bot.service"
sudo tee "$SERVICE_FILE" > /dev/null <<EOF
[Unit]
Description=Telegram Bot Service
After=network.target

[Service]
Type=simple
User=$(whoami)
WorkingDirectory=$BOT_DIR
ExecStart=$BOT_DIR/venv/bin/python $BOT_DIR/bot.py
Restart=always
RestartSec=5
Environment=HTTP_PORT=$HTTP_PORT
Environment=SERVER_BASE_URL=

[Install]
WantedBy=multi-user.target
EOF

sudo systemctl daemon-reload
sudo systemctl enable telegram-bot.service

# ─── Firewall ───────────────────────────────────────────────────────────
echo "  Configuring firewall..."
sudo ufw allow ${HTTP_PORT}/tcp 2>/dev/null || true

echo ""
echo "============================================"
echo "  Installation complete!"
echo "============================================"
echo ""
echo "  Start bot:  sudo systemctl start telegram-bot"
echo "  Status:     sudo systemctl status telegram-bot"
echo "  Stop:       sudo systemctl stop telegram-bot"
echo "  Logs:       sudo journalctl -u telegram-bot -f"
echo ""
echo "  HTTP Port:  $HTTP_PORT"
echo "  Direct URL: http://YOUR_SERVER_IP:$HTTP_PORT/downloads/FILENAME"
echo ""
echo "  After install, send /start to your bot on Telegram."
echo "  Make sure port $HTTP_PORT is open in your firewall."
echo ""
