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
echo "[1/8] Installing system packages..."
sudo apt update -qq
sudo apt install -y -qq python3 python3-pip python3-venv ffmpeg git curl wget unzip

# ─── 1b. Check Python version (need 3.9+) ──────────────────────────────
PY_VER=$(python3 -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')")
PY_MAJOR=$(echo "$PY_VER" | cut -d. -f1)
PY_MINOR=$(echo "$PY_VER" | cut -d. -f2)
if [ "$PY_MAJOR" -lt 3 ] || { [ "$PY_MAJOR" -eq 3 ] && [ "$PY_MINOR" -lt 9 ]; }; then
    echo "  Python $PY_VER is too old. Installing Python 3.11..."
    sudo apt install -y -qq software-properties-common
    sudo add-apt-repository -y ppa:deadsnakes/ppa
    sudo apt update -qq
    sudo apt install -y -qq python3.11 python3.11-venv python3.11-distutils
    PYTHON=python3.11
else
    echo "  Python $PY_VER is OK."
    PYTHON=python3
fi

# ─── 2. Deno runtime (for TikTok downloads) ──────────────────────────────
echo "[2/8] Installing Deno runtime..."
if ! command -v deno &> /dev/null; then
    curl -fsSL https://deno.land/install.sh | sh
    echo 'export DENO_INSTALL="$HOME/.deno"' >> ~/.bashrc
    echo 'export PATH="$DENO_INSTALL/bin:$PATH"' >> ~/.bashrc
    export DENO_INSTALL="$HOME/.deno"
    export PATH="$DENO_INSTALL/bin:$PATH"
    echo "  Deno installed: $(deno --version | head -1)"
else
    echo "  Deno already installed: $(deno --version | head -1)"
fi

# ─── 3. Python virtual environment ──────────────────────────────────────
echo "[3/8] Creating Python virtual environment..."
cd "$BOT_DIR"
$PYTHON -m venv venv
source venv/bin/activate

# ─── 4. Install Python packages ─────────────────────────────────────────
echo "[4/8] Installing Python packages..."
pip install -q --upgrade pip
pip install -q python-telegram-bot==21.6 yt-dlp instaloader cloudscraper curl_cffi beautifulsoup4 lxml Pillow qrcode[pil] rembg requests jdatetime hijridate

# ─── 5. Create directories ──────────────────────────────────────────────
echo "[5/8] Creating required directories..."
cd "$BOT_DIR"
mkdir -p data downloads/admin_dl downloads/qrcodes downloads/photos downloads/instagram downloads/videos

# ─── 6. Update bot.py with token ────────────────────────────────────────
echo "[6/8] Setting bot token in bot.py..."
if [ -f bot.py ]; then
    sed -i "s/BOT_TOKEN = \".*\"/BOT_TOKEN = \"$BOT_TOKEN\"/" bot.py
    echo "  Done."
else
    echo "  WARNING: bot.py not found!"
    exit 1
fi

# ─── 7. Create config files in data/ ────────────────────────────────────
echo "[7/8] Creating config files in data/..."

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

# ─── 8. Create systemd service ──────────────────────────────────────────
echo "[8/8] Creating systemd service..."
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
