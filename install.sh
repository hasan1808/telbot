#!/bin/bash
set -e

# ─── Telegram Bot Installer ────────────────────────────────────────────
# usage: bash install.sh <bot_token> <admin_id> [port] [bot_directory]
#
#   bot_token    - Telegram Bot Token (اجباری)
#   admin_id     - آیدی عددی ادمین (اجباری)
#   port         - پورت HTTP سرور (اختیاری، پیش‌فرض 8585)
#   bot_directory- مسیر نصب (اختیاری، پیش‌فرض پوشه فعلی)

if [ $# -lt 2 ]; then
    echo "❌ خطا: توکن ربات و آیدی ادمین الزامی است."
    echo ""
    echo "روش استفاده:"
    echo "  bash install.sh <BOT_TOKEN> <ADMIN_ID> [PORT] [DIRECTORY]"
    echo ""
    echo "مثال:"
    echo "  bash install.sh 123456:ABC-DEF1234gh 876139114 8585 /opt/bot"
    exit 1
fi

BOT_TOKEN="$1"
ADMIN_ID="$2"
HTTP_PORT="${3:-8585}"
BOT_DIR="${4:-$(pwd)}"
ADMIN_IDS="[$ADMIN_ID]"

echo "============================================"
echo "  Telegram Bot - در حال نصب..."
echo "============================================"
echo "  توکن:        ${BOT_TOKEN:0:20}..."
echo "  آیدی ادمین:  $ADMIN_ID"
echo "  پورت HTTP:   $HTTP_PORT"
echo "  مسیر نصب:    $BOT_DIR"
echo "============================================"
echo ""

# ─── 1. System packages ─────────────────────────────────────────────────
echo "[1/6] به‌روزرسانی و نصب بسته‌های سیستمی..."
sudo apt update -qq
sudo apt install -y -qq python3 python3-pip python3-venv ffmpeg git curl wget

# ─── 2. Python virtual environment ──────────────────────────────────────
echo "[2/6] ایجاد محیط مجازی Python..."
cd "$BOT_DIR"
python3 -m venv venv
source venv/bin/activate

# ─── 3. Install Python packages ─────────────────────────────────────────
echo "[3/6] نصب کتابخانه‌های Python..."
pip install -q --upgrade pip
pip install -q python-telegram-bot==22.8 yt-dlp instaloader cloudscraper curl_cffi beautifulsoup4 lxml Pillow qrcode[pil] rembg

# ─── 4. Create config files ─────────────────────────────────────────────
echo "[4/6] ایجاد فایل‌های پیکربندی..."
cd "$BOT_DIR"

# Update bot.py with the token
if [ -f bot.py ]; then
    sed -i "s/BOT_TOKEN = .*/BOT_TOKEN = \"$BOT_TOKEN\"/" bot.py
    sed -i "s/HTTP_PORT = .*/HTTP_PORT = $HTTP_PORT/" bot.py
    echo "  ✅ توکن و پورت در bot.py تنظیم شد."
else
    echo "  ⚠️  فایل bot.py وجود ندارد. لطفاً دستی تنظیم کنید."
fi

# admin.json
cat > admin.json <<EOF
{
  "admins": $ADMIN_IDS,
  "banned": []
}
EOF

# users.json
cat > users.json <<EOF
{"users": []}
EOF

# limits.json
cat > limits.json <<EOF
{}
EOF

# config.json
cat > config.json <<EOF
{
  "bot_enabled": true,
  "force_channel": "",
  "welcome_message": "",
  "file_expiry": 3600,
  "max_upload": 2048,
  "auto_delete": false,
  "language": "fa",
  "default_quality": "best",
  "daily_limit": 10,
  "port": $HTTP_PORT,
  "base_url": "",
  "new_user_notify": true
}
EOF

# short_urls.json
cat > short_urls.json <<EOF
{}
EOF

# ─── 5. Create systemd service ──────────────────────────────────────────
echo "[5/6] ایجاد سرویس systemd..."
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

# ─── 6. Firewall ────────────────────────────────────────────────────────
echo "[6/6] تنظیم فایروال..."
sudo ufw allow ${HTTP_PORT}/tcp 2>/dev/null || true

echo ""
echo "============================================"
echo "  ✅ نصب با موفقیت کامل شد!"
echo "============================================"
echo ""
echo "  شروع ربات:   sudo systemctl start telegram-bot"
echo "  وضعیت:       sudo systemctl status telegram-bot"
echo "  توقف:        sudo systemctl stop telegram-bot"
echo "  لاگ:         sudo journalctl -u telegram-bot -f"
echo ""
echo "  پورت HTTP:   $HTTP_PORT"
echo "  آپلود فایل:  http://YOUR_SERVER_IP:$HTTP_PORT/upload"
echo ""
