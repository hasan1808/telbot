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
pip install -q python-telegram-bot==22.8 yt-dlp instaloader cloudscraper curl_cffi beautifulsoup4 lxml Pillow qrcode[pil] rembg requests jdatetime hijridate

# ─── 4. Create directories ──────────────────────────────────────────────
echo "[4/7] ایجاد پوشه‌های مورد نیاز..."
cd "$BOT_DIR"
mkdir -p data downloads/admin_dl downloads/qrcodes downloads/photos downloads/instagram downloads/videos

# ─── 5. Update bot.py with token ────────────────────────────────────────
echo "[5/7] تنظیم توکن ربات..."
if [ -f bot.py ]; then
    sed -i "s/BOT_TOKEN = \".*\"/BOT_TOKEN = \"$BOT_TOKEN\"/" bot.py
    echo "  ✅ توکن در bot.py تنظیم شد."
else
    echo "  ⚠️  فایل bot.py وجود ندارد!"
    exit 1
fi

# ─── 6. Create config files in data/ ────────────────────────────────────
echo "[6/7] ایجاد فایل‌های پیکربندی..."

# config.json
cat > data/config.json <<EOF
{
  "http_port": $HTTP_PORT
}
EOF

# admin.json
cat > data/admin.json <<EOF
{
  "admins": $ADMIN_IDS,
  "banned": []
}
EOF

# users.json
cat > data/users.json <<EOF
{"users": []}
EOF

# limits.json
cat > data/limits.json <<EOF
{}
EOF

# short_urls.json
cat > data/short_urls.json <<EOF
{}
EOF

# instagram_login.json
cat > data/instagram_login.json <<EOF
{"username": "", "password": ""}
EOF

echo "  ✅ فایل‌های پیکربندی در data/ ایجاد شد."

# ─── 7. Create systemd service ──────────────────────────────────────────
echo "[7/7] ایجاد سرویس systemd..."
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
echo "    تنظیم فایروال..."
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
echo "  آپلود فایل:  http://YOUR_SERVER_IP:$HTTP_PORT/"
echo "  لینک مستقیم: http://YOUR_SERVER_IP:$HTTP_PORT/downloads/FILENAME"
echo ""
echo "  📌 بعد از نصب، حتماً در ربات /start رو بزنید"
echo "     و پورت 8585 رو در فایروال سرور باز کنید."
echo ""
