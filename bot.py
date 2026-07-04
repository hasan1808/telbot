import asyncio
import json
import os
import re
import shutil
import socket
import subprocess
import sys
import threading
import time
from http.server import HTTPServer, SimpleHTTPRequestHandler
from pathlib import Path
from urllib.parse import quote, unquote

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.error import BadRequest
from telegram.ext import (
    Application,
    CommandHandler,
    CallbackQueryHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

import jdatetime
import hijridate

# ─── Config ───────────────────────────────────────────────────────────────
BOT_TOKEN = os.environ.get("BOT_TOKEN", "8648205951:AAG0x6GDqAgDaZXPBHDnX1XvdTuJ8i8ltik")
DATA_DIR = Path(__file__).parent / "data"
ADMIN_FILE = DATA_DIR / "admin.json"
USERS_FILE = DATA_DIR / "users.json"
DOWNLOAD_DIR = Path(__file__).parent / "downloads"
MAX_TG_FILE = 11 * 1024 * 1024 * 1024  # 11GB limit for direct link
MAX_TELEGRAM_UPLOAD = 50 * 1024 * 1024  # 50MB for sending via Telegram
CLEANUP_INTERVAL = 300  # cleanup every 5 minutes
HTTP_PORT = int(os.environ.get("HTTP_PORT", "8080"))
SERVER_BASE_URL = os.environ.get("SERVER_BASE_URL", "")
IG_SESSION_FILE = DATA_DIR / "instagram_session"
IG_LOGIN_FILE = DATA_DIR / "instagram_login.json"
LIMITS_FILE = DATA_DIR / "limits.json"
CONFIG_FILE = DATA_DIR / "config.json"
SHORT_URLS_FILE = DATA_DIR / "short_urls.json"

DOWNLOAD_DIR.mkdir(exist_ok=True)
DATA_DIR.mkdir(exist_ok=True)


def load_limits():
    return load_json(LIMITS_FILE)


def save_limits(data):
    save_json(LIMITS_FILE, data)


def get_daily_limit():
    return load_limits().get("daily_limit", 10)


def set_daily_limit(n):
    d = load_limits()
    d["daily_limit"] = n
    save_limits(d)


def get_usage_today(uid):
    today = time.strftime("%Y-%m-%d")
    return load_limits().get("usage", {}).get(str(uid), {}).get(today, 0)


def increment_usage(uid):
    today = time.strftime("%Y-%m-%d")
    d = load_limits()
    usage = d.setdefault("usage", {})
    user_usage = usage.setdefault(str(uid), {})
    user_usage[today] = user_usage.get(today, 0) + 1
    save_limits(d)


def check_daily_limit(uid):
    """Return True if user can use the bot, False if limit reached."""
    if uid in load_admins():
        return True  # no limit for admins
    return get_usage_today(uid) < get_daily_limit()


# ─── Config (bot_enabled, force_channel) ─────────────────────────────────
def load_config():
    return load_json(CONFIG_FILE)


def save_config(data):
    save_json(CONFIG_FILE, data)


def get_config(key, default=None):
    return load_config().get(key, default)


def set_config(key, value):
    d = load_config()
    d[key] = value
    save_config(d)


def is_bot_enabled():
    d = load_config()
    return d.get("bot_enabled", True)


def set_bot_enabled(val):
    d = load_config()
    d["bot_enabled"] = val
    save_config(d)


def get_force_channel():
    return load_config().get("force_channel", "")


def set_force_channel(val):
    d = load_config()
    d["force_channel"] = val
    save_config(d)


async def check_force_join(user_id, context):
    channel = get_force_channel()
    if not channel:
        return True
    try:
        member = await context.bot.get_chat_member(chat_id=channel, user_id=user_id)
        return member.status in ("member", "administrator", "creator")
    except:
        return False


# ─── Extended config getters ────────────────────────────────────────────
def get_welcome_message():
    return load_config().get("welcome_message", "")


def set_welcome_message(val):
    set_config("welcome_message", val)


def get_file_expiry():
    return load_config().get("file_expiry", 3600)


def set_file_expiry(val):
    d = load_config()
    d["file_expiry"] = max(60, val)
    save_config(d)


def get_max_upload():
    return load_config().get("max_upload_mb", 2048)


def set_max_upload(val):
    d = load_config()
    d["max_upload_mb"] = max(1, val)
    save_config(d)


def get_auto_delete():
    return load_config().get("auto_delete", False)


def set_auto_delete(val):
    d = load_config()
    d["auto_delete"] = val
    save_config(d)


def get_language():
    return load_config().get("language", "fa")


def set_language(val):
    set_config("language", val)


def get_default_quality():
    return load_config().get("default_quality", "best")


def set_default_quality(val):
    set_config("default_quality", val)


def get_new_user_notify():
    return load_config().get("new_user_notify", True)


def set_new_user_notify(val):
    d = load_config()
    d["new_user_notify"] = val
    save_config(d)


def get_http_port():
    return load_config().get("http_port", int(os.environ.get("HTTP_PORT", "8080")))


def set_http_port(val):
    d = load_config()
    d["http_port"] = max(1, min(65535, val))
    save_config(d)


def get_base_url_config():
    return load_config().get("base_url", os.environ.get("SERVER_BASE_URL", ""))


def set_base_url_config(val):
    set_config("base_url", val)


def get_local_ip():
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except:
        return "127.0.0.1"


def get_base_url():
    b = get_base_url_config()
    if b:
        return b.rstrip("/")
    return f"http://{get_local_ip()}:{get_http_port()}"


def make_direct_url(relative_path):
    """Create a URL-safe download link from a relative Path object."""
    parts = relative_path.as_posix().split("/")
    encoded = "/".join(quote(p, safe="") for p in parts)
    return f"{get_base_url()}/{encoded}"


class FileUploadHandler(SimpleHTTPRequestHandler):
    def log_message(self, format, *args):
        pass

    def translate_path(self, path):
        return str(Path.cwd() / unquote(path.lstrip("/")))

    def do_GET(self):
        # Short URL redirect
        if self.path.startswith("/s/"):
            code = self.path.split("/s/")[-1].split("/")[0]
            if code:
                target = resolve_short_url(code)
                if target:
                    self.send_response(302)
                    self.send_header("Location", target)
                    self.end_headers()
                    return
            self.send_response(404)
            self.end_headers()
            self.wfile.write(b"Short URL not found")
            return
        if self.path == "/upload":
            max_mb = get_max_upload()
            html = f"""<!DOCTYPE html>
<html lang="fa" dir="rtl">
<head><meta charset="utf-8"><title>آپلود فایل</title>
<style>
body{{font-family:Tahoma,sans-serif;max-width:600px;margin:50px auto;padding:20px;background:#f0f2f5}}
.card{{background:#fff;padding:30px;border-radius:12px;box-shadow:0 2px 10px rgba(0,0,0,.1)}}
h2{{color:#1a73e8;margin-top:0}}
input[type=file]{{width:100%;padding:15px;margin:10px 0;border:2px dashed #ddd;border-radius:8px;background:#fafafa}}
button{{background:#1a73e8;color:#fff;border:none;padding:12px 30px;border-radius:8px;font-size:16px;cursor:pointer}}
button:hover{{background:#1557b0}}
.success{{color:#0a7c3e;background:#e6f7ee;padding:12px;border-radius:8px;margin:10px 0}}
.error{{color:#c5221f;background:#fce8e6;padding:12px;border-radius:8px;margin:10px 0}}
</style></head>
<body>
<div class="card">
<h2>📁 آپلود فایل (تا {max_mb} مگابایت)</h2>
<form action="/upload" method="post" enctype="multipart/form-data">
<input type="file" name="file" required>
<button type="submit">📤 آپلود کن</button>
</form>
</div>
</body></html>"""
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", len(html.encode()))
            self.end_headers()
            self.wfile.write(html.encode())
            return
        super().do_GET()

    def do_POST(self):
        if self.path == "/upload":
            content_length = int(self.headers.get("Content-Length", 0))
            content_type = self.headers.get("Content-Type", "")
            boundary = content_type.split("boundary=")[-1].encode()
            body = self.rfile.read(content_length)

            # Parse multipart form data
            filename = "uploaded_file"
            file_data = None
            for part in body.split(b"--" + boundary):
                if b"Content-Disposition" not in part:
                    continue
                if b'filename="' in part or b"filename='" in part:
                    # Extract filename
                    for line in part.split(b"\r\n"):
                        if b"filename=" in line:
                            fn = line.split(b"filename=")[-1].strip(b'"\' \r\n').decode()
                            if fn:
                                filename = fn
                            break
                    # Extract file data (after double CRLF)
                    parts = part.split(b"\r\n\r\n", 1)
                    if len(parts) > 1:
                        file_data = parts[1].strip(b"\r\n")

            if file_data:
                uid = int(time.time())
                dl_dir = DOWNLOAD_DIR / "uploads" / str(uid)
                dl_dir.mkdir(parents=True, exist_ok=True)
                file_path = dl_dir / filename
                with open(file_path, "wb") as f:
                    f.write(file_data)
                size_mb = len(file_data) / 1024 / 1024
                relative_path = file_path.absolute().relative_to(Path.cwd())
                direct_url = make_direct_url(relative_path)

                result_html = f"""<!DOCTYPE html>
<html lang="fa" dir="rtl">
<head><meta charset="utf-8"><title>نتیجه آپلود</title>
<style>
body{{font-family:Tahoma,sans-serif;max-width:600px;margin:50px auto;padding:20px;background:#f0f2f5}}
.card{{background:#fff;padding:30px;border-radius:12px;box-shadow:0 2px 10px rgba(0,0,0,.1)}}
.success{{color:#0a7c3e;background:#e6f7ee;padding:12px;border-radius:8px}}
a{{color:#1a73e8;word-break:break-all}}
</style></head>
<body><div class="card">
<div class="success">✅ آپلود موفق!</div>
<p>نام فایل: <b>{filename}</b></p>
<p>حجم: <b>{size_mb:.1f} MB</b></p>
<p>🔗 لینک دانلود:</p>
<p><a href="{direct_url}" target="_blank">{direct_url}</a></p>
<p><a href="/upload">🔄 آپلود فایل جدید</a></p>
</div></body></html>"""
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", len(result_html.encode()))
                self.end_headers()
                self.wfile.write(result_html.encode())
            else:
                self.send_response(400)
                self.end_headers()
                self.wfile.write(b"No file uploaded")
            return
        self.send_response(404)
        self.end_headers()


def start_http_server():
    port = get_http_port()
    server = HTTPServer(("0.0.0.0", port), FileUploadHandler)
    print(f"HTTP server running on port {port}")
    print(f"Upload page: http://{get_local_ip()}:{port}/upload")
    server.serve_forever()

# ─── JSON helpers ────────────────────────────────────────────────────────
def load_json(path):
    if path.exists():
        with open(path) as f:
            return json.load(f)
    return {}


def save_json(path, data):
    with open(path, "w") as f:
        json.dump(data, f, indent=2)


def load_admins():
    return load_json(ADMIN_FILE).get("admins", [])


def save_admins(admins):
    save_json(ADMIN_FILE, {"admins": admins, "banned": load_banned()})


def load_banned():
    return load_json(ADMIN_FILE).get("banned", [])


def save_banned(banned):
    d = load_json(ADMIN_FILE)
    save_json(ADMIN_FILE, {"admins": d.get("admins", []), "banned": banned})


def load_user_ids():
    """Return list of user dicts with 'id' key. Backward-compat with old int-only format."""
    raw = load_json(USERS_FILE).get("users", [])
    result = []
    for item in raw:
        if isinstance(item, int):
            result.append({"id": item, "name": str(item)})
        elif isinstance(item, dict):
            result.append(item)
    return result


def get_user_id_set():
    """Return set of user IDs for quick lookup."""
    return set(u["id"] for u in load_user_ids())


def save_user_ids(users):
    save_json(USERS_FILE, {"users": users})


def is_admin(uid):
    return uid in load_admins()


# ─── Calendar converter helpers ─────────────────────────────────────────
def shamsi_to_miladi(year, month, day):
    d = jdatetime.date(year, month, day)
    g = d.togregorian()
    return g.year, g.month, g.day

def miladi_to_shamsi(year, month, day):
    d = jdatetime.date.fromgregorian(year=year, month=month, day=day)
    return d.year, d.month, d.day

def shamsi_to_ghamari(year, month, day):
    g = jdatetime.date(year, month, day).togregorian()
    h = hijridate.Gregorian(g.year, g.month, g.day).to_hijri()
    return h.year, h.month, h.day

def ghamari_to_shamsi(year, month, day):
    g = hijridate.Hijri(year, month, day).to_gregorian()
    d = jdatetime.date.fromgregorian(year=g.year, month=g.month, day=g.day)
    return d.year, d.month, d.day


# ─── Currency rate helpers ──────────────────────────────────────────────
def fetch_tgju_rates():
    import urllib.request
    from bs4 import BeautifulSoup
    try:
        req = urllib.request.Request("https://www.tgju.org/", headers={"User-Agent": "Mozilla/5.0"})
        r = urllib.request.urlopen(req, timeout=15)
        soup = BeautifulSoup(r.read().decode("utf-8"), "lxml")
        data = {}

        for table in soup.find_all("table"):
            for row in table.find_all("tr"):
                th = row.find("th")
                price_td = row.find("td", class_="nf")
                if th and price_td:
                    name = th.get_text(strip=True)
                    price_text = price_td.get_text(strip=True).replace(",", "").replace("\u066c", "").strip()
                    try:
                        data[name] = float(price_text)
                    except:
                        pass

        for tr in soup.find_all("tr"):
            th = tr.find("th")
            if not th:
                continue
            name = th.get_text(strip=True)
            if name in data:
                continue
            for td in tr.find_all("td"):
                price_text = td.get_text(strip=True).replace(",", "").replace("\u066c", "").strip()
                if price_text and price_text[0].isdigit():
                    try:
                        price = float(price_text)
                        if 100 < price < 1e12:
                            data[name] = price
                            break
                    except:
                        pass

        return data
    except:
        return None

def fetch_trx_rate(usd_irr):
    """Fetch TRX price in IRR using jsdelivr API."""
    import urllib.request, json
    try:
        r = urllib.request.urlopen("https://cdn.jsdelivr.net/npm/@fawazahmed0/currency-api@latest/v1/currencies/usd.json", timeout=8)
        c = json.loads(r.read())["usd"]
        trx_per_usd = c.get("trx", 0)
        if trx_per_usd and usd_irr:
            return int(usd_irr / trx_per_usd)
    except:
        pass
    return None


# ─── Keyboards ───────────────────────────────────────────────────────────
def main_menu(uid=None):
    keyboard = [
        [InlineKeyboardButton("🎬  دانلود ویدیو  🎬", callback_data="section_download")],
        [InlineKeyboardButton("📱  اطلاعات گوشی  📱", callback_data="section_gsm")],
        [InlineKeyboardButton("🧰  ابزارها  🧰", callback_data="section_tools")],
        [InlineKeyboardButton("📦  آپلود فایل و لینک مستقیم  📦", callback_data="section_filehost")],
    ]
    if uid and is_admin(uid):
        keyboard.append([InlineKeyboardButton("⚙️  تنظیمات ربات  ⚙️", callback_data="section_settings")])
        keyboard.append([InlineKeyboardButton("📣  ارسال پیام همگانی  📣", callback_data="section_broadcast")])
    return InlineKeyboardMarkup(keyboard)


def settings_menu():
    keyboard = [
        [InlineKeyboardButton("👥  مدیریت  👥", callback_data="settings_cat_management")],
        [InlineKeyboardButton("🤖  تنظیمات ربات  🤖", callback_data="settings_cat_bot")],
        [InlineKeyboardButton("🔧  تنظیمات پیشرفته  🔧", callback_data="settings_advanced")],
        [InlineKeyboardButton("🔐  حساب‌ها  🔐", callback_data="settings_cat_accounts")],
        [InlineKeyboardButton("🔄  ری‌استارت ربات  🔄", callback_data="settings_restart")],
        [InlineKeyboardButton("🔙  برگشت به منو  🔙", callback_data="back_main")],
    ]
    return InlineKeyboardMarkup(keyboard)


def settings_cat_management():
    keyboard = [
        [InlineKeyboardButton("👥  مدیریت کاربران  👥", callback_data="section_users")],
        [InlineKeyboardButton("📊  آمار ربات  📊", callback_data="section_stats")],
        [InlineKeyboardButton("📂  مدیریت فایل‌ها  📂", callback_data="section_files")],
        [InlineKeyboardButton("🔙  برگشت  🔙", callback_data="section_settings")],
    ]
    return InlineKeyboardMarkup(keyboard)


def settings_cat_bot():
    limit = get_daily_limit()
    bot_status = "🟢 فعال" if is_bot_enabled() else "🔴 غیرفعال"
    channel = get_force_channel()
    channel_status = f"✅ {channel}" if channel else "⛔ خالی"
    lang = "🇮🇷 فارسی" if get_language() == "fa" else "🇬🇧 English"
    keyboard = [
        [InlineKeyboardButton(f"🔘  وضعیت ربات: {bot_status}", callback_data="settings_toggle_bot")],
        [InlineKeyboardButton(f"📌  محدودیت روزانه: {limit}", callback_data="settings_limit")],
        [InlineKeyboardButton(f"📢  کانال اجباری: {channel_status}", callback_data="settings_force_channel")],
        [InlineKeyboardButton(f"🌐  زبان: {lang}", callback_data="adv_language")],
        [InlineKeyboardButton("🔙  برگشت  🔙", callback_data="section_settings")],
    ]
    return InlineKeyboardMarkup(keyboard)


def settings_cat_accounts():
    keyboard = [
        [InlineKeyboardButton("🔐  حساب اینستاگرام  🔐", callback_data="settings_instagram")],
        [InlineKeyboardButton("🔙  برگشت  🔙", callback_data="section_settings")],
    ]
    return InlineKeyboardMarkup(keyboard)


def advanced_settings_menu():
    expiry_h = get_file_expiry() // 3600
    max_up = get_max_upload()
    auto_del = "✅ فعال" if get_auto_delete() else "❌ غیرفعال"
    lang = "🇮🇷 فارسی" if get_language() == "fa" else "🇬🇧 English"
    quality = get_default_quality()
    notify = "✅ فعال" if get_new_user_notify() else "❌ غیرفعال"
    port = get_http_port()
    domain = get_base_url_config() or "❌ تنظیم نشده"
    quality_map = {"best": "بهترین کیفیت", "1080": "1080p", "720": "720p", "480": "480p"}
    quality_label = quality_map.get(quality, quality)
    keyboard = [
        [InlineKeyboardButton("✏️  پیغام خوش‌آمدگویی  ✏️", callback_data="adv_welcome")],
        [InlineKeyboardButton(f"⏱  زمان انقضا: {expiry_h}h", callback_data="adv_expiry")],
        [InlineKeyboardButton(f"📏  حداکثر حجم: {max_up}MB", callback_data="adv_maxupload")],
        [InlineKeyboardButton(f"🔇  حذف خودکار: {auto_del}", callback_data="adv_autodelete")],
        [InlineKeyboardButton(f"🌐  زبان: {lang}", callback_data="adv_language")],
        [InlineKeyboardButton(f"📥  کیفیت: {quality_label}", callback_data="adv_quality")],
        [InlineKeyboardButton(f"🔔  اعلان کاربر جدید: {notify}", callback_data="adv_notify")],
        [InlineKeyboardButton(f"🔌  پورت: {port}", callback_data="adv_port")],
        [InlineKeyboardButton(f"🌍  دامنه: {domain}", callback_data="adv_domain")],
        [InlineKeyboardButton("🔙  برگشت  🔙", callback_data="section_settings")],
    ]
    return InlineKeyboardMarkup(keyboard)


def users_menu():
    admins = load_admins()
    banned = load_banned()
    all_users = load_user_ids()
    keyboard = [
        [InlineKeyboardButton(f"👑  ادمین‌ها ({len(admins)})  👑", callback_data="list_admins")],
        [InlineKeyboardButton(f"👤  کاربران ({len(all_users)})  👤", callback_data="list_users")],
        [InlineKeyboardButton(f"⛔  بن شده‌ها ({len(banned)})  ⛔", callback_data="list_banned")],
        [InlineKeyboardButton("➕  اضافه کردن ادمین  ➕", callback_data="add_admin_prompt")],
        [InlineKeyboardButton("➖  حذف ادمین  ➖", callback_data="remove_admin_prompt")],
        [InlineKeyboardButton("🔙  برگشت  🔙", callback_data="back_main")],
    ]
    return InlineKeyboardMarkup(keyboard)


def back_main_kb():
    return InlineKeyboardMarkup([[InlineKeyboardButton("🔙  برگشت  🔙", callback_data="back_main")]])


def format_size(bytes):
    for unit in ("B", "KB", "MB", "GB"):
        if bytes < 1024:
            return f"{bytes:.1f}{unit}"
        bytes /= 1024
    return f"{bytes:.1f}TB"


async def show_file_list(query, uid, context, page=0):
    if not is_admin(uid):
        await safe_edit(query, "⛔ دسترسی ندارید", reply_markup=main_menu(uid))
        return
    PER_PAGE = 10
    all_files = sorted(DOWNLOAD_DIR.rglob("*"), key=lambda f: f.stat().st_mtime, reverse=True)
    all_files = [f for f in all_files if f.is_file()]
    total = len(all_files)
    total_pages = max(1, (total + PER_PAGE - 1) // PER_PAGE)
    page = max(0, min(page, total_pages - 1))
    start = page * PER_PAGE
    end = start + PER_PAGE
    page_files = all_files[start:end]

    # Store file list for deletion
    context.user_data["file_list"] = [str(f) for f in all_files]

    text = f"📂 **مدیریت فایل‌ها**\n\n"
    text += f"🗂 مجموع: {total} فایل\n"
    text += f"📦 حجم کل: {format_size(sum(f.stat().st_size for f in all_files))}\n"
    text += f"📄 صفحه {page + 1} از {total_pages}\n\n"

    if not page_files:
        text += "هیچ فایلی وجود ندارد."
    else:
        for i, f in enumerate(page_files, start + 1):
            rel = f.relative_to(DOWNLOAD_DIR)
            size = format_size(f.stat().st_size)
            text += f"{i}. `{rel}` ({size})\n"

    keyboard = []
    if page_files:
        for i, f in enumerate(page_files, start + 1):
            keyboard.append([InlineKeyboardButton(f"🗑 حذف {i}", callback_data=f"file_del_{i - 1}")])
        row = []
        if page > 0:
            row.append(InlineKeyboardButton("◀️ قبلی", callback_data=f"files_page_{page - 1}"))
        if page < total_pages - 1:
            row.append(InlineKeyboardButton("بعدی ▶️", callback_data=f"files_page_{page + 1}"))
        if row:
            keyboard.append(row)
    if total > 0:
        keyboard.append([InlineKeyboardButton("🗑 حذف همه فایل‌ها", callback_data="files_del_all")])
    keyboard.append([InlineKeyboardButton("🔙  برگشت  🔙", callback_data="section_settings")])

    await safe_edit(query, text, parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(keyboard))


def download_menu():
    keyboard = [
        [InlineKeyboardButton("▶️  یوتیوب  ▶️", callback_data="dl_youtube")],
        [InlineKeyboardButton("📸  اینستاگرام  📸", callback_data="dl_instagram")],
        [InlineKeyboardButton("🎵  تیک‌تاک  🎵", callback_data="dl_tiktok")],
        [InlineKeyboardButton("🔙  برگشت  🔙", callback_data="back_main")],
    ]
    return InlineKeyboardMarkup(keyboard)

# ─── Commands ────────────────────────────────────────────────────────────
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    users = load_user_ids()
    user_ids = get_user_id_set()

    # Migrate old integer entries to dict format
    migrated = False
    for i, u in enumerate(users):
        if isinstance(u, int):
            users[i] = {"id": u, "name": str(u), "username": ""}
            migrated = True
    if migrated:
        save_user_ids(users)
        user_ids = get_user_id_set()

    # New user or update info
    found = False
    for i, u in enumerate(users):
        if isinstance(u, dict) and u["id"] == user.id:
            if u.get("name") != user.full_name or u.get("username") != (user.username or ""):
                users[i] = {"id": user.id, "name": user.full_name, "username": user.username or ""}
                save_user_ids(users)
            found = True
            break

    if not found:
        users.append({"id": user.id, "name": user.full_name, "username": user.username or ""})
        save_user_ids(users)
        if get_new_user_notify():
            admins = load_admins()
            for aid in admins:
                try:
                    await context.bot.send_message(
                        chat_id=aid,
                        text=f"🆕 **کاربر جدید**\n\n👤 نام: {user.full_name}\n🆔 آیدی: `{user.id}`",
                        parse_mode="Markdown"
                    )
                except:
                    pass

    if not is_bot_enabled() and not is_admin(user.id):
        await update.message.reply_text("⛔ ربات موقتاً غیرفعال شده است. بعداً امتحان کنید.")
        return

    if not is_admin(user.id) and not await check_force_join(user.id, context):
        channel = get_force_channel()
        await update.message.reply_text(
            f"🔒 برای استفاده از ربات باید عضو کانال زیر بشی:\n{channel}",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔗 عضویت در کانال", url=channel)]])
        )
        return

    # Custom welcome message
    welcome = get_welcome_message()
    if welcome:
        lang = get_language()
        if lang == "fa":
            text = welcome
        else:
            text = welcome
        await update.message.reply_text(text, reply_markup=main_menu(user.id))
        return

    url_text = f"🔗 سرور دانلود: {get_base_url()}\n\n" if is_admin(user.id) else ""
    await update.message.reply_text(
        f"به ربات مدیریت خوش آمدید {user.first_name}!\n\n{url_text}"
        "لطفاً یکی از گزینه‌های زیر را انتخاب کنید:",
        reply_markup=main_menu(user.id),
    )


async def status_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    msg = f"🆔 آیدی شما: `{user.id}`\n"
    msg += f"👤 نام: {user.full_name}\n"
    msg += "✅ شما ادمین هستید" if is_admin(user.id) else "❌ شما ادمین نیستید"
    await update.message.reply_text(msg, parse_mode="Markdown")


async def help_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("از دکمه‌های منو استفاده کنید. /start برای نمایش منو")

async def safe_edit(query, text, reply_markup=None, parse_mode=None, disable_web_page_preview=None):
    """Edit message text; fall back to delete+send if no text exists."""
    try:
        await query.edit_message_text(text, reply_markup=reply_markup,
            parse_mode=parse_mode, disable_web_page_preview=disable_web_page_preview)
    except BadRequest as e:
        if "no text" in str(e).lower():
            await query.message.delete()
            await query.message.reply_text(text, reply_markup=reply_markup,
                parse_mode=parse_mode, disable_web_page_preview=disable_web_page_preview)
        else:
            raise

# ─── Button handler ──────────────────────────────────────────────────────
async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    data = query.data
    uid = query.from_user.id

    if data == "back_main":
        await safe_edit(query, "لطفاً یکی از گزینه‌های زیر را انتخاب کنید:", reply_markup=main_menu(uid))

    elif data == "section_users":
        if not is_admin(uid):
            await safe_edit(query, "⛔ دسترسی ندارید", reply_markup=main_menu(uid))
            return
        await safe_edit(query, "👥 **مدیریت کاربران و ادمین‌ها**", parse_mode="Markdown", reply_markup=users_menu())

    elif data == "section_download":
        if not is_admin(uid):
            if not is_bot_enabled():
                await safe_edit(query, "⛔ ربات موقتاً غیرفعال شده است.", reply_markup=main_menu(uid))
                return
            if not await check_force_join(uid, context):
                channel = get_force_channel()
                await safe_edit(query, 
                    f"🔒 برای استفاده از ربات باید عضو کانال زیر بشی:\n{channel}",
                    reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔗 عضویت در کانال", url=channel)]])
                )
                return
        await safe_edit(query, 
            "📥 **دانلود از شبکه‌های اجتماعی**\n\n"
            "روی گزینه مورد نظر کلیک کنید و لینک را ارسال کنید.",
            parse_mode="Markdown", reply_markup=download_menu()
        )

    elif data == "section_filehost":
        if not is_admin(uid):
            if not is_bot_enabled():
                await safe_edit(query, "⛔ ربات موقتاً غیرفعال شده است.", reply_markup=main_menu(uid))
                return
            if not await check_force_join(uid, context):
                channel = get_force_channel()
                await safe_edit(query, 
                    f"🔒 برای استفاده از ربات باید عضو کانال زیر بشی:\n{channel}",
                    reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔗 عضویت در کانال", url=channel)]])
                )
                return
        context.user_data["awaiting_file_upload"] = True
        text = "📁 **آپلود فایل و دریافت لینک دانلود**\n\nفایل رو بفرست (تا ۵۰ مگ از طریق تلگرام).\nبعد از آپلود، لینک دانلود مستقیم بهت می‌دم."
        kb = [[InlineKeyboardButton("🔙 برگشت", callback_data="back_main")]]
        if is_admin(uid):
            text += "\n\n🔹 ادمین: می‌تونی لینک دانلود هم بفرستی (تا ۱۱ گیگ)."
            kb.insert(0, [InlineKeyboardButton("📥 دانلود از لینک", callback_data="admin_dl_from_url")])
        await safe_edit(query, text, parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup(kb))

    elif data == "admin_dl_from_url":
        context.user_data["awaiting_admin_dl"] = True
        await safe_edit(query,
            "📥 **دانلود از لینک (ادمین)**\n\n"
            "لینک مستقیم فایل رو بفرست.\n"
            "ربات دانلود می‌کنه و لینک مستقیم بهت میده.\n"
            "📦 حداکثر حجم: ۱۱ گیگابایت",
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 برگشت", callback_data="section_filehost")]]))

    elif data == "section_gsm":
        keyboard = [
            [InlineKeyboardButton("🔍 جستجو با نام مدل", callback_data="gsm_by_name")],
            [InlineKeyboardButton("📸 جستجو با عکس", callback_data="gsm_by_photo")],
            [InlineKeyboardButton("🔙 برگشت", callback_data="back_main")],
        ]
        await safe_edit(query,
            "📱 **اطلاعات گوشی از GSMArena**\n\n"
            "نام مدل را بفرستید یا با عکس جستجو کنید:",
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup(keyboard))

    elif data == "gsm_by_name":
        context.user_data["awaiting_gsm"] = True
        await safe_edit(query,
            "🔍 نام مدل گوشی را وارد کنید.\n"
            "مثال: `Samsung Galaxy S24`\n"
            "یا: `SM-S928B`",
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 برگشت", callback_data="section_gsm")]])
        )

    elif data == "section_settings":
        if not is_admin(uid):
            await safe_edit(query, "⛔ دسترسی ندارید", reply_markup=main_menu(uid))
            return
        await safe_edit(query, 
            "⚙️ **تنظیمات**\n\nیک دسته را انتخاب کنید:",
            parse_mode="Markdown", reply_markup=settings_menu()
        )

    elif data == "settings_cat_management":
        await safe_edit(query, 
            "👥 **مدیریت**",
            parse_mode="Markdown", reply_markup=settings_cat_management()
        )

    elif data == "settings_cat_bot":
        await safe_edit(query, 
            "🤖 **تنظیمات ربات**",
            parse_mode="Markdown", reply_markup=settings_cat_bot()
        )

    elif data == "settings_cat_accounts":
        await safe_edit(query, 
            "🔐 **حساب‌ها**",
            parse_mode="Markdown", reply_markup=settings_cat_accounts()
        )

    elif data == "settings_limit":
        limit = get_daily_limit()
        keyboard = [
            [InlineKeyboardButton("➕  افزایش  ➕", callback_data="settings_limit_up")],
            [InlineKeyboardButton("➖  کاهش  ➖", callback_data="settings_limit_down")],
            [InlineKeyboardButton("🔙  برگشت  🔙", callback_data="section_settings")],
        ]
        await safe_edit(query, 
            f"📌 **محدودیت روزانه کاربران**\n\n"
            f"تعداد مجاز استفاده هر کاربر در روز: **{limit}**\n\n"
            f"ادمین‌ها محدودیت ندارند.\n"
            f"می‌توانید با دکمه‌های زیر یا دستور /setlimit عدد مقدار رو تغییر بدید.",
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup(keyboard)
        )

    elif data == "settings_limit_up":
        limit = get_daily_limit() + 1
        set_daily_limit(limit)
        await safe_edit(query, 
            f"✅ محدودیت به {limit} افزایش یافت.",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙  برگشت  🔙", callback_data="settings_limit")]])
        )

    elif data == "settings_limit_down":
        limit = max(1, get_daily_limit() - 1)
        set_daily_limit(limit)
        await safe_edit(query, 
            f"✅ محدودیت به {limit} کاهش یافت.",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙  برگشت  🔙", callback_data="settings_limit")]])
        )

    elif data == "settings_instagram":
        ig_status = "❌ لاگین نشده"
        if IG_SESSION_FILE.exists():
            ig_status = "✅ لاگین شده"
        elif IG_LOGIN_FILE.exists():
            ig_status = "✅ رمز ذخیره شده"
        text = (
            f"🔐 **وضعیت اینستاگرام:** {ig_status}\n\n"
        )
        text += "برای دانلود از اینستاگرام باید لاگین باشی.\n"
        text += "از دکمه زیر برای ورود استفاده کن."
        keyboard = [
            [InlineKeyboardButton("🔑  ورود با یوزرنیم و رمز  🔑", callback_data="dl_instagram_login_prompt")],
            [InlineKeyboardButton("🔙  برگشت  🔙", callback_data="section_settings")],
        ]
        if IG_SESSION_FILE.exists() or IG_LOGIN_FILE.exists():
            keyboard.insert(0, [InlineKeyboardButton("🚪  خروج از حساب  🚪", callback_data="settings_instagram_logout")])
        await safe_edit(query, text, parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(keyboard))

    elif data == "settings_instagram_logout":
        if IG_SESSION_FILE.exists():
            IG_SESSION_FILE.unlink()
        if IG_LOGIN_FILE.exists():
            IG_LOGIN_FILE.unlink()
        await safe_edit(query, "✅ از اینستاگرام خارج شدید.", reply_markup=settings_menu())

    elif data == "settings_toggle_bot":
        new_val = not is_bot_enabled()
        set_bot_enabled(new_val)
        status = "فعال" if new_val else "غیرفعال"
        await safe_edit(query, 
            f"✅ ربات {status} شد.\n\n"
            f"کاربران عادی {'می‌توانند' if new_val else 'نمی‌توانند'} از ربات استفاده کنند.",
            reply_markup=settings_menu()
        )

    elif data == "settings_force_channel":
        channel = get_force_channel()
        text = "📢 **عضویت اجباری کانال**\n\n"
        if channel:
            text += f"✅ کانال تنظیم شده: `{channel}`\n\n"
            text += "کاربران قبل از استفاده باید عضو این کانال شوند."
        else:
            text += "هیچ کانالی تنظیم نشده.\n\n"
            text += "با زدن دکمه تنظیم، نام کاربری کانال را وارد کنید (با @)."
        keyboard = [
            [InlineKeyboardButton("✏️ تنظیم کانال", callback_data="settings_force_channel_prompt")],
        ]
        if channel:
            keyboard.append([InlineKeyboardButton("🗑 حذف کانال", callback_data="settings_force_channel_clear")])
        keyboard.append([InlineKeyboardButton("🔙 برگشت", callback_data="section_settings")])
        await safe_edit(query, text, parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(keyboard))

    elif data == "settings_force_channel_prompt":
        context.user_data["awaiting_force_channel"] = True
        await safe_edit(query, 
            "✏️ نام کاربری کانال را با @ بفرستید.\nمثال: `@my_channel`",
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 انصراف", callback_data="settings_force_channel")]])
        )

    elif data == "settings_force_channel_clear":
        set_force_channel("")
        await safe_edit(query, 
            "✅ کانال حذف شد. دیگر عضویت اجباری وجود ندارد.",
            reply_markup=settings_menu()
        )

    elif data == "section_files":
        await show_file_list(query, uid, context, 0)

    elif data.startswith("files_page_"):
        page = int(data.split("_")[2])
        await show_file_list(query, uid, context, page)

    elif data.startswith("file_del_"):
        idx = int(data.split("_")[2])
        filepath_str = context.user_data.get("file_list", [])[idx] if context.user_data.get("file_list") else ""
        if filepath_str:
            fp = Path(filepath_str)
            if fp.exists():
                fp.unlink()
        await show_file_list(query, uid, context, 0)

    elif data == "files_del_all":
        import shutil
        for item in DOWNLOAD_DIR.iterdir():
            if item.is_dir():
                shutil.rmtree(item, ignore_errors=True)
            else:
                try:
                    item.unlink()
                except:
                    pass
        await safe_edit(query, "✅ همه فایل‌ها پاک شدند.", reply_markup=settings_menu())

    elif data == "settings_advanced":
        await safe_edit(query, 
            "🛠 **تنظیمات پیشرفته**",
            parse_mode="Markdown", reply_markup=advanced_settings_menu()
        )

    elif data == "adv_welcome":
        context.user_data["awaiting_welcome_msg"] = True
        current = get_welcome_message()
        text = "✏️ **پیغام خوش‌آمدگویی**\n\n"
        text += "متن دلخواه برای پیام /start را ارسال کنید.\n"
        text += "برای تنظیم مجدد به حالت پیش‌فرض، کلمه `reset` را بفرستید.\n\n"
        if current:
            text += f"📄 متن فعلی:\n`{current[:200]}`"
        else:
            text += "📄 متن فعلی: پیش‌فرض (بدون تنظیم)"
        await safe_edit(query, 
            text, parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙  انصراف  🔙", callback_data="settings_advanced")]])
        )

    elif data == "adv_expiry":
        h = get_file_expiry() // 3600
        keyboard = [
            [InlineKeyboardButton("➕  ۱ ساعت  ➕", callback_data="adv_expiry_up")],
            [InlineKeyboardButton("➖  ۱ ساعت  ➖", callback_data="adv_expiry_down")],
            [InlineKeyboardButton("🔙  برگشت  🔙", callback_data="settings_advanced")],
        ]
        await safe_edit(query, 
            f"⏱ **زمان انقضای فایل‌ها**\n\n"
            f"مدت اعتبار لینک‌های دانلود: **{h} ساعت**\n\n"
            f"بعد از این مدت، فایل‌ها پاک می‌شوند.",
            parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(keyboard)
        )

    elif data == "adv_expiry_up":
        h = get_file_expiry() // 3600 + 1
        set_file_expiry(h * 3600)
        await safe_edit(query, 
            f"✅ زمان انقضا به {h} ساعت افزایش یافت.",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙  برگشت  🔙", callback_data="adv_expiry")]])
        )

    elif data == "adv_expiry_down":
        h = max(1, get_file_expiry() // 3600 - 1)
        set_file_expiry(h * 3600)
        await safe_edit(query, 
            f"✅ زمان انقضا به {h} ساعت کاهش یافت.",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙  برگشت  🔙", callback_data="adv_expiry")]])
        )

    elif data == "adv_maxupload":
        mb = get_max_upload()
        keyboard = [
            [InlineKeyboardButton("➕  ۱۰۰MB  ➕", callback_data="adv_maxupload_up")],
            [InlineKeyboardButton("➖  ۱۰۰MB  ➖", callback_data="adv_maxupload_down")],
            [InlineKeyboardButton("🔙  برگشت  🔙", callback_data="settings_advanced")],
        ]
        await safe_edit(query, 
            f"📏 **حداکثر حجم آپلود**\n\n"
            f"حداکثر حجم فایل از طریق HTTP: **{mb} MB**\n\n"
            f"آپلود از طریق تلگرام همچنان ۵۰MB محدودیت دارد.",
            parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(keyboard)
        )

    elif data == "adv_maxupload_up":
        mb = get_max_upload() + 100
        set_max_upload(mb)
        await safe_edit(query, 
            f"✅ حداکثر حجم آپلود به {mb}MB افزایش یافت.",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙  برگشت  🔙", callback_data="adv_maxupload")]])
        )

    elif data == "adv_maxupload_down":
        mb = max(100, get_max_upload() - 100)
        set_max_upload(mb)
        await safe_edit(query, 
            f"✅ حداکثر حجم آپلود به {mb}MB کاهش یافت.",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙  برگشت  🔙", callback_data="adv_maxupload")]])
        )

    elif data == "adv_autodelete":
        new_val = not get_auto_delete()
        set_auto_delete(new_val)
        status = "فعال" if new_val else "غیرفعال"
        await safe_edit(query, 
            f"✅ حذف خودکار درخواست‌ها {status} شد.\n\n"
            f"پیام‌های کاربران بعد از پردازش پاک خواهند شد.",
            reply_markup=advanced_settings_menu()
        )

    elif data == "adv_language":
        new_lang = "en" if get_language() == "fa" else "fa"
        set_language(new_lang)
        label = "🇮🇷 فارسی" if new_lang == "fa" else "🇬🇧 English"
        await safe_edit(query, 
            f"✅ زبان به {label} تغییر یافت.",
            reply_markup=advanced_settings_menu()
        )

    elif data == "adv_quality":
        current = get_default_quality()
        options = ["best", "1080", "720", "480"]
        idx = (options.index(current) + 1) % len(options) if current in options else 0
        new_q = options[idx]
        set_default_quality(new_q)
        label_map = {"best": "بهترین کیفیت", "1080": "1080p", "720": "720p", "480": "480p"}
        await safe_edit(query, 
            f"✅ کیفیت پیش‌فرض به **{label_map[new_q]}** تغییر یافت.",
            parse_mode="Markdown",
            reply_markup=advanced_settings_menu()
        )

    elif data == "adv_notify":
        new_val = not get_new_user_notify()
        set_new_user_notify(new_val)
        status = "فعال" if new_val else "غیرفعال"
        await safe_edit(query, 
            f"✅ اعلان کاربر جدید {status} شد.\n\n"
            f"وقتی کاربر جدید ربات را استارت کند، {'به ادمین پیام داده می‌شود' if new_val else 'پیامی ارسال نمی‌شود'}.",
            reply_markup=advanced_settings_menu()
        )

    elif data == "adv_port":
        context.user_data["awaiting_port"] = True
        current = get_http_port()
        await safe_edit(query, 
            f"🔌 **تغییر پورت HTTP**\n\n"
            f"پورت فعلی: `{current}`\n\n"
            f"یک عدد پورت جدید (۱ تا ۶۵۵۳۵) بفرستید.\n"
            f"⚠️ تغییر پورت نیاز به ری‌استارت ربات دارد.",
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙  انصراف  🔙", callback_data="settings_advanced")]])
        )

    elif data == "adv_domain":
        context.user_data["awaiting_domain"] = True
        current = get_base_url_config() or "تنظیم نشده"
        await safe_edit(query, 
            f"🌍 **تنظیم دامنه**\n\n"
            f"دامنه فعلی: `{current}`\n\n"
            f"آدرس کامل دامنه یا IP را بفرستید.\n"
            f"مثال: `https://mydomain.com`\n"
            f"برای پاک کردن دامنه، کلمه `reset` را بفرستید.",
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙  انصراف  🔙", callback_data="settings_advanced")]])
        )

    elif data == "settings_restart":
        keyboard = [
            [InlineKeyboardButton("✅  بله، ری‌استارت کن  ✅", callback_data="settings_restart_confirm")],
            [InlineKeyboardButton("❌  انصراف  ❌", callback_data="section_settings")],
        ]
        await safe_edit(query, 
            "🔄 **ری‌استارت ربات**\n\n"
            "آیا مطمئنی؟ ربات قطع شده و دوباره راه‌اندازی می‌شود.",
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup(keyboard)
        )

    elif data == "settings_restart_confirm":
        await safe_edit(query, "🔄 ربات در حال ری‌استارت...")
        # Write restart flag for wrapper script
        (Path(__file__).parent / "restart.flag").touch()
        # Spawn new process and exit
        subprocess.Popen(
            [sys.executable, __file__],
            cwd=str(Path(__file__).parent),
            shell=False,
        )
        os._exit(0)

    elif data == "dl_youtube":
        context.user_data["download_mode"] = "youtube"
        await safe_edit(query, 
            "▶️ لینک ویدیوی یوتیوب را ارسال کنید.\nمثال: https://youtube.com/watch?v=...",
            reply_markup=back_main_kb()
        )

    elif data == "dl_instagram":
        ig_status = "❌ لاگین نشده"
        if IG_SESSION_FILE.exists():
            ig_status = "✅ لاگین شده"
        elif IG_LOGIN_FILE.exists():
            ig_status = "✅ رمز ذخیره شده"
        keyboard = [
            [InlineKeyboardButton("📤 ارسال لینک برای دانلود", callback_data="dl_instagram_send")],
            [InlineKeyboardButton(f"🔑 وضعیت: {ig_status}", callback_data="dl_instagram_status")],
            [InlineKeyboardButton("🔙 برگشت", callback_data="back_main")],
        ]
        await safe_edit(query, 
            "📸 **دانلود از اینستاگرام**\n\n"
            "برای دانلود، اول باید لاگین کنی. "
            "از دکمه وضعیت می‌تونی ببینی لاگین هستی یا نه.",
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup(keyboard)
        )

    elif data == "dl_instagram_send":
        context.user_data["download_mode"] = "instagram"
        if not IG_SESSION_FILE.exists() and not IG_LOGIN_FILE.exists():
            keyboard = [
                [InlineKeyboardButton("🔑 ورود با یوزرنیم و رمز", callback_data="dl_instagram_login_prompt")],
                [InlineKeyboardButton("🔙 برگشت", callback_data="dl_instagram")],
            ]
            await safe_edit(query, 
                "⚠️ اول باید لاگین کنی.\n"
                "روی دکمه زیر کلیک کن و اطلاعات رو وارد کن:",
                reply_markup=InlineKeyboardMarkup(keyboard)
            )
            return
        await safe_edit(query, 
            "📸 لینک پست یا ریلز اینستاگرام را ارسال کنید.\n"
            "مثال: https://instagram.com/p/...",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 برگشت", callback_data="dl_instagram")]])
        )

    elif data == "dl_instagram_status":
        session_exists = IG_SESSION_FILE.exists()
        login_exists = IG_LOGIN_FILE.exists()
        text = "📊 **وضعیت اینستاگرام:**\n\n"
        text += f"✅ نشست ذخیره شده: {'فعال' if session_exists else 'غیرفعال'}\n"
        text += f"✅ رمز ذخیره شده: {'فعال' if login_exists else 'غیرفعال'}\n\n"
        if session_exists or login_exists:
            text += "برای خروج از حساب: /iglogout"
        else:
            text += "برای ورود: /iglogin username password"
        keyboard = [
            [InlineKeyboardButton("🔑 ورود با یوزرنیم و رمز", callback_data="dl_instagram_login_prompt")],
            [InlineKeyboardButton("🔙 برگشت", callback_data="dl_instagram")],
        ]
        await safe_edit(query, text, parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(keyboard))

    elif data == "dl_instagram_login_prompt":
        context.user_data["awaiting_ig_login"] = True
        await safe_edit(query, 
            "🔑 یوزرنیم و رمز اینستاگرام رو به این صورت بفرست:\n"
            "`username password`\n\n"
            "مثال: `myaccount mypass123`\n\n"
            "⚠️ این اطلاعات فقط روی سرور ذخیره می‌شه.",
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 انصراف", callback_data="dl_instagram")]])
        )

    elif data == "dl_tiktok":
        context.user_data["download_mode"] = "tiktok"
        await safe_edit(query, 
            "🎵 لینک ویدیوی تیک‌تاک را ارسال کنید.\nمثال: https://vm.tiktok.com/...",
            reply_markup=back_main_kb()
        )

    elif data == "section_stats":
        if not is_admin(uid):
            await safe_edit(query, "⛔ دسترسی ندارید", reply_markup=main_menu(uid))
            return
        all_users = load_user_ids()
        admins = load_admins()
        banned = load_banned()
        text = (
            f"📊 **آمار ربات**\n\n"
            f"👤 کل کاربران: {len(all_users)}\n"
            f"👑 ادمین‌ها: {len(admins)}\n"
            f"⛔ بن شده‌ها: {len(banned)}"
        )
        await safe_edit(query, text, parse_mode="Markdown", reply_markup=back_main_kb())

    elif data == "section_broadcast":
        if not is_admin(uid):
            await safe_edit(query, "⛔ دسترسی ندارید", reply_markup=main_menu(uid))
            return
        context.user_data["awaiting_broadcast"] = True
        await safe_edit(query, 
            "📢 متن پیام همگانی را ارسال کنید:",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 انصراف", callback_data="back_main")]])
        )

    elif data == "list_admins":
        if not is_admin(uid):
            return
        admins = load_admins()
        if not admins:
            text = "📭 هیچ ادمینی وجود ندارد."
        else:
            text = "👑 **لیست ادمین‌ها:**\n\n"
            for i, a in enumerate(admins, 1):
                all_users = load_user_ids()
                found = next((u for u in all_users if isinstance(u, dict) and u["id"] == a), None)
                if found and found.get("name") and found["name"] != str(a):
                    label = esc_md(found["name"])
                    if found.get("username"):
                        label += f" - @{esc_md(found['username'])}"
                    text += f"{i}. {label}\n   🆔 `{a}`\n"
                else:
                    text += f"{i}. `{a}`\n"
        await safe_edit(query, text, parse_mode="Markdown", reply_markup=users_menu())

    elif data == "list_users":
        if not is_admin(uid):
            return
        all_users = load_user_ids()
        banned = load_banned()
        if not all_users:
            text = "📭 هیچ کاربری وجود ندارد."
        else:
            text = "👤 **لیست کاربران:**\n\n"
            for i, u in enumerate(all_users[:50], 1):
                uid_num = u["id"] if isinstance(u, dict) else u
                name = u.get("name", "") if isinstance(u, dict) else ""
                uname = u.get("username", "") if isinstance(u, dict) else ""
                status = "⛔" if uid_num in banned else "✅"
                parts = []
                if name and name != str(uid_num):
                    parts.append(esc_md(name))
                if uname:
                    parts.append(f"@{esc_md(uname)}")
                label = " - ".join(parts) if parts else str(uid_num)
                text += f"{i}. {label}\n   🆔 `{uid_num}` {status}\n"
            if len(all_users) > 50:
                text += f"\n... و {len(all_users) - 50} کاربر دیگر"
        await safe_edit(query, text, parse_mode="Markdown", reply_markup=users_menu())

    elif data == "list_banned":
        if not is_admin(uid):
            return
        banned = load_banned()
        if not banned:
            text = "✅ هیچ کاربری بن نشده است."
        else:
            text = "⛔ **لیست بن شده‌ها:**\n\n"
            for i, b in enumerate(banned, 1):
                text += f"{i}. `{b}`\n"
            kb = [[InlineKeyboardButton("🔓 آنبن همه", callback_data="unban_all")]]
            kb += [[InlineKeyboardButton("🔙 برگشت", callback_data="section_users")]]
            await safe_edit(query, text, parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(kb))
            return
        await safe_edit(query, text, parse_mode="Markdown", reply_markup=users_menu())

    elif data == "unban_all":
        save_banned([])
        await safe_edit(query, "✅ همه کاربران آنبن شدند.", reply_markup=users_menu())

    elif data == "add_admin_prompt":
        context.user_data["awaiting_add_admin"] = True
        await safe_edit(query, 
            "🆔 آیدی عددی کاربر جدید را ارسال کنید:",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 انصراف", callback_data="section_users")]])
        )

    elif data == "remove_admin_prompt":
        context.user_data["awaiting_remove_admin"] = True
        await safe_edit(query, 
            "🆔 آیدی عددی ادمین مورد نظر را ارسال کنید:",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 انصراف", callback_data="section_users")]])
        )

    elif data == "section_photoedit":
        keyboard = [
            [InlineKeyboardButton("✨ افزایش کیفیت", callback_data="photo_upscale")],
            [InlineKeyboardButton("🎨 حذف بکگراند", callback_data="photo_removebg")],
            [InlineKeyboardButton("🔙 برگشت", callback_data="section_tools")],
        ]
        await safe_edit(query,
            "🖼 **ویرایش عکس**\n\n"
            "یکی از گزینه‌های زیر را انتخاب کنید و سپس عکس را ارسال کنید.",
            parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(keyboard))

    elif data == "photo_upscale":
        context.user_data["awaiting_photo_upscale"] = True
        await safe_edit(query,
            "✨ **افزایش کیفیت عکس**\n\n"
            "عکس مورد نظر را ارسال کنید.\n"
            "کیفیت عکس ۲ برابر افزایش می‌یابد.",
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 برگشت", callback_data="section_photoedit")]]))

    elif data == "photo_removebg":
        context.user_data["awaiting_photo_removebg"] = True
        await safe_edit(query,
            "🎨 **حذف بکگراند عکس**\n\n"
            "عکس مورد نظر را ارسال کنید.\n"
            "بکگراند عکس حذف شده و به صورت PNG با پس‌زمینه شفاف دریافت می‌کنید.",
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 برگشت", callback_data="section_photoedit")]]))

    # ── Tools section ──
    elif data == "section_tools":
        keyboard = [
            [InlineKeyboardButton("🖼 ویرایش عکس", callback_data="section_photoedit")],
            [InlineKeyboardButton("🔄 تبدیل رسانه", callback_data="tools_convert")],
            [InlineKeyboardButton("📱 QR کد", callback_data="tools_qr")],
            [InlineKeyboardButton("🔗 کوتاه‌کننده لینک", callback_data="tools_shorten")],
            [InlineKeyboardButton("📅 تبدیل تاریخ", callback_data="tools_calendar")],
            [InlineKeyboardButton("💰 نرخ ارز و طلا", callback_data="tools_currency")],
            [InlineKeyboardButton("🔙 برگشت", callback_data="back_main")],
        ]
        await safe_edit(query,
            "🧰 **ابزارها**\n\n"
            "یکی از ابزارهای زیر را انتخاب کنید:",
            parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(keyboard))

    elif data == "tools_convert":
        keyboard = [
            [InlineKeyboardButton("🖼 تبدیل فرمت عکس", callback_data="tools_convert_image")],
            [InlineKeyboardButton("🎞 ویدیو به GIF", callback_data="tools_convert_v2g")],
            [InlineKeyboardButton("🎵 استخراج صوت از ویدیو", callback_data="tools_convert_audio")],
            [InlineKeyboardButton("🔙 برگشت", callback_data="section_tools")],
        ]
        await safe_edit(query,
            "🔄 **تبدیل رسانه**\n\n"
            "یکی از گزینه‌ها را انتخاب کنید و فایل را ارسال کنید:",
            parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(keyboard))

    elif data == "tools_convert_image":
        context.user_data["awaiting_convert"] = "image"
        await safe_edit(query,
            "🖼 **تبدیل فرمت عکس**\n\n"
            "عکس را ارسال کنید.\n"
            "پس از دریافت، فرمت مقصد را انتخاب می‌کنید.",
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 برگشت", callback_data="tools_convert")]]))

    elif data == "tools_convert_v2g":
        context.user_data["awaiting_convert"] = "video_to_gif"
        await safe_edit(query,
            "🎞 **تبدیل ویدیو به GIF**\n\n"
            "ویدیو را ارسال کنید.",
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 برگشت", callback_data="tools_convert")]]))

    elif data == "tools_convert_audio":
        context.user_data["awaiting_convert"] = "extract_audio"
        await safe_edit(query,
            "🎵 **استخراج صوت از ویدیو**\n\n"
            "ویدیو را ارسال کنید.\n"
            "صوت به فرمت MP3 استخراج می‌شود.",
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 برگشت", callback_data="tools_convert")]]))

    elif data == "tools_qr":
        context.user_data["awaiting_qr"] = True
        await safe_edit(query,
            "📱 **ساخت QR کد**\n\n"
            "متن یا لینک مورد نظر را ارسال کنید تا QR کد آن ساخته شود.",
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 برگشت", callback_data="section_tools")]]))

    elif data == "tools_shorten":
        context.user_data["awaiting_shorten"] = True
        await safe_edit(query,
            "🔗 **کوتاه‌کننده لینک**\n\n"
            "لینک مورد نظر را ارسال کنید تا نسخه کوتاه شده آن ساخته شود.\n"
            "مثال: `https://example.com/very/long/url`",
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 برگشت", callback_data="section_tools")]]))

    # ── Calendar converter ──
    elif data == "tools_calendar":
        keyboard = [
            [InlineKeyboardButton("📅 شمسی به میلادی", callback_data="cal_shamsi_to_miladi")],
            [InlineKeyboardButton("📅 میلادی به شمسی", callback_data="cal_miladi_to_shamsi")],
            [InlineKeyboardButton("📅 شمسی به قمری", callback_data="cal_shamsi_to_ghamari")],
            [InlineKeyboardButton("📅 قمری به شمسی", callback_data="cal_ghamari_to_shamsi")],
            [InlineKeyboardButton("🔙 برگشت", callback_data="section_tools")],
        ]
        await safe_edit(query,
            "📅 **تبدیل تاریخ**\n\n"
            "نوع تبدیل را انتخاب کنید:",
            parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(keyboard))

    elif data == "cal_shamsi_to_miladi":
        context.user_data["awaiting_calendar"] = ("shamsi_to_miladi", "1403/01/21")
        await safe_edit(query,
            "📅 **شمسی به میلادی**\n\n"
            "تاریخ شمسی را به فرمت `سال/ماه/روز` وارد کنید.\n"
            "مثال: `1403/01/21`",
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 برگشت", callback_data="tools_calendar")]]))

    elif data == "cal_miladi_to_shamsi":
        context.user_data["awaiting_calendar"] = ("miladi_to_shamsi", "2024/04/09")
        await safe_edit(query,
            "📅 **میلادی به شمسی**\n\n"
            "تاریخ میلادی را به فرمت `سال/ماه/روز` وارد کنید.\n"
            "مثال: `2024/04/09`",
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 برگشت", callback_data="tools_calendar")]]))

    elif data == "cal_shamsi_to_ghamari":
        context.user_data["awaiting_calendar"] = ("shamsi_to_ghamari", "1403/01/21")
        await safe_edit(query,
            "📅 **شمسی به قمری**\n\n"
            "تاریخ شمسی را به فرمت `سال/ماه/روز` وارد کنید.\n"
            "مثال: `1403/01/21`",
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 برگشت", callback_data="tools_calendar")]]))

    elif data == "cal_ghamari_to_shamsi":
        context.user_data["awaiting_calendar"] = ("ghamari_to_shamsi", "1446/10/06")
        await safe_edit(query,
            "📅 **قمری به شمسی**\n\n"
            "تاریخ قمری را به فرمت `سال/ماه/روز` وارد کنید.\n"
            "مثال: `1446/10/06`",
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 برگشت", callback_data="tools_calendar")]]))

    # ── Currency rates ──
    elif data == "tools_currency":
        await safe_edit(query, "⏳ در حال دریافت نرخ ارز و طلا از tgju...")
        tgju = await asyncio.to_thread(fetch_tgju_rates)
        if tgju:
            usd = tgju.get("دلار", 0)
            eur = tgju.get("یورو", 0) or tgju.get("EUR/USD", 0) and int(usd * 1.14) or 0
            gbp = tgju.get("پوند انگلیس", 0)
            aed = tgju.get("درهم امارات", 0)
            try_l = tgju.get("لیر ترکیه", 0)
            gold_usd = tgju.get("انس طلا", 0)
            gold_18 = tgju.get("طلای 18 عیار", 0)
            gold_mesghal = tgju.get("مثقال طلا", 0)
            coin_emami = tgju.get("سکه امامی", 0)
            coin_bahar = tgju.get("سکه بهار آزادی", 0)
            btc = tgju.get("بیت کوین", 0)
            tether = tgju.get("تتر", 0)

            lines = ["💰 **نرخ ارز و طلا**\n"]
            if usd:
                lines.append(f"💵 دلار (USD): `{int(usd):,}` ریال")
            if eur:
                lines.append(f"💶 یورو (EUR): `{int(eur):,}` ریال")
            if gbp:
                lines.append(f"💷 پوند (GBP): `{int(gbp):,}` ریال")
            if aed:
                lines.append(f"🇦🇪 درهم (AED): `{int(aed):,}` ریال")
            if try_l:
                lines.append(f"🇹🇷 لیر (TRY): `{int(try_l):,}` ریال")
            if tether:
                lines.append(f"🪙 تتر (USDT): `{int(tether):,}` ریال")
            if usd:
                trx = await asyncio.to_thread(fetch_trx_rate, usd)
                if trx:
                    lines.append(f"⚡ ترون (TRX): `{int(trx):,}` ریال")

            lines.append("")
            if gold_18:
                lines.append(f"🥇 طلای ۱۸ عیار: `{int(gold_18):,}` ریال")
            if gold_mesghal:
                lines.append(f"🥇 مثقال طلا: `{int(gold_mesghal):,}` ریال")
            if gold_usd:
                lines.append(f"🌍 انس طلا: `${gold_usd:,.2f}`")
            if coin_emami:
                lines.append(f"🪙 سکه امامی: `{int(coin_emami):,}` ریال")
            if coin_bahar:
                lines.append(f"🪙 سکه بهار آزادی: `{int(coin_bahar):,}` ریال")
            if btc:
                lines.append(f"₿ بیت‌کوین: `${btc:,.2f}`")

            lines.append(f"\n📅 {time.strftime('%Y-%m-%d %H:%M')}")
            lines.append("منبع: tgju.org")
            text = "\n".join(lines)
        else:
            text = "❌ خطا در دریافت نرخ‌ها از tgju.org"
        await safe_edit(query, text, parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔄 تازه‌سازی", callback_data="tools_currency"),
                                                  InlineKeyboardButton("🔙 برگشت", callback_data="section_tools")]]))

    elif data == "gsm_by_photo":
        context.user_data["awaiting_gsm_photo"] = True
        await safe_edit(query,
            "📱 **جستجوی گوشی با عکس**\n\n"
            "عکس گوشی مورد نظر را ارسال کنید.\n"
            "ربات با جستجوی معکوس، مدل گوشی را پیدا می‌کند.",
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 برگشت", callback_data="section_gsm")]]))

    # Image conversion format selection
    elif data.startswith("conv_img_"):
        fmt = data.split("_")[2].upper()
        input_path = context.user_data.get("awaiting_convert_file")
        if not input_path:
            await safe_edit(query, "❌ فایل یافت نشد. دوباره عکس را ارسال کنید.", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 برگشت", callback_data="tools_convert")]]))
            return
        input_path = Path(input_path)
        output_path = input_path.with_suffix(f".{fmt.lower()}")
        try:
            await safe_edit(query, f"⏳ در حال تبدیل به {fmt}...")
            await asyncio.to_thread(convert_image_format, str(input_path), str(output_path), fmt)
            with open(output_path, "rb") as f:
                await query.message.reply_document(
                    document=f, filename=f"converted.{fmt.lower()}",
                    caption=f"✅ تبدیل به {fmt} انجام شد.",
                    reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 برگشت", callback_data="tools_convert")]])
                )
            await query.message.delete()
            try:
                input_path.unlink()
                output_path.unlink()
            except: pass
        except Exception as e:
            await safe_edit(query, f"❌ خطا:\n`{str(e)[:200]}`", parse_mode="Markdown")

# ─── Download handler ───────────────────────────────────────────────────
COOKIES_FILE = Path(__file__).parent / "cookies.txt"


INSTAGRAM_DOC = "https://github.com/yt-dlp/yt-dlp/wiki/FAQ#how-do-i-pass-cookies-to-yt-dlp"


def download_instagram(url, output_dir):
    """Download Instagram post, return filepath."""
    shortcode = None
    m = re.search(r"instagram\.com/(?:p|reel|tv)/([^/?]+)", url)
    if m:
        shortcode = m.group(1)
    if not shortcode:
        raise Exception("لینک اینستاگرام معتبر نیست.")

    # Try instaloader with saved session/login
    import instaloader
    L = instaloader.Instaloader(
        download_videos=True,
        compress_json=False,
        filename_pattern="%(filename)s",
    )

    if IG_SESSION_FILE.exists():
        try:
            L.load_session_from_file("", IG_SESSION_FILE)
        except:
            pass

    if IG_LOGIN_FILE.exists() and not L.context.is_logged_in:
        creds = load_json(IG_LOGIN_FILE)
        try:
            L.login(creds["username"], creds["password"])
            L.save_session_to_file(IG_SESSION_FILE)
        except Exception as e:
            pass

    if L.context.is_logged_in:
        try:
            post = instaloader.Post.from_shortcode(L.context, shortcode)
            L.download_post(post, target=str(output_dir))
            for f in sorted(output_dir.iterdir(), key=lambda x: x.stat().st_mtime, reverse=True):
                if f.suffix in (".mp4", ".mkv", ".webm"):
                    return f
        except Exception as e:
            raise Exception(f"اینستاگرام: {e}")

    # Fallback: try yt-dlp with cookies
    raise Exception(
        "برای دانلود اینستاگرام باید لاگین کنی.\n"
        "روش ۱: /iglogin username password\n"
        "روش ۲: فایل cookies.txt رو با /cookies آپلود کن"
    )


def download_video(url, output_dir, platform=None):
    """Download video using yt-dlp, return (filepath) or raises."""
    output_template = str(output_dir / "%(title).100s_%(id)s.%(ext)s")
    quality = get_default_quality()
    quality_format = {"best": "best[filesize<50M]/best", "1080": "bestvideo[height<=1080][filesize<45M]+bestaudio/best[height<=1080][filesize<50M]/best", "720": "bestvideo[height<=720][filesize<45M]+bestaudio/best[height<=720][filesize<50M]/best", "480": "bestvideo[height<=480][filesize<45M]+bestaudio/best[height<=480][filesize<50M]/best"}
    format_str = quality_format.get(quality, "best[filesize<50M]/best")
    cmd = [
        "yt-dlp",
        "-f", format_str,
        "--merge-output-format", "mp4",
        "--no-playlist",
        "--no-warnings",
        "--print", "after_move:filepath",
        "--js-runtimes", "deno",
        "-o", output_template,
    ]
    # Cookies handling for Instagram
    if platform == "instagram":
        if COOKIES_FILE.exists():
            cmd += ["--cookies", str(COOKIES_FILE)]
        else:
            # Try to kill browser processes and extract cookies
            import subprocess as sp
            for proc in ("msedge", "chrome", "firefox"):
                sp.run(f"taskkill /f /im {proc}.exe 2>nul", shell=True, capture_output=True)
            for browser in ("edge", "chrome", "firefox"):
                cmd += ["--cookies-from-browser", browser]
                break
    cmd.append(url)
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=300)
    if result.returncode != 0:
        raise Exception(result.stderr.strip() or "Download failed")
    # Find the actual file in output_dir (most recently modified .mp4)
    mp4_files = sorted(output_dir.glob("*.mp4"), key=lambda f: f.stat().st_mtime, reverse=True)
    if mp4_files:
        return mp4_files[0]
    # Fallback: search wider
    all_files = sorted(output_dir.rglob("*"), key=lambda f: f.stat().st_mtime, reverse=True)
    for f in all_files:
        if f.is_file() and f.suffix in (".mp4", ".mkv", ".webm", ".mov"):
            return f
    raise Exception("File not found after download")


async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    text = update.message.text.strip()

    if uid in load_banned():
        await update.message.reply_text("⛔ شما بن شده‌اید.")
        return

    # Bot enabled check
    if not is_bot_enabled() and not is_admin(uid):
        await update.message.reply_text("⛔ ربات موقتاً غیرفعال شده است. بعداً امتحان کنید.")
        return

    # Force join check
    if not is_admin(uid) and not await check_force_join(uid, context):
        ch = get_force_channel()
        join_msg = f"🔒 برای استفاده از ربات باید عضو کانال زیر بشی:\n\n{ch}"
        await update.message.reply_text(
            join_msg,
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔗 عضویت در کانال", url=ch)]])
        )
        return

    # ── Admin text handlers ──
    # Instagram login from menu
    if context.user_data.get("awaiting_ig_login"):
        context.user_data["awaiting_ig_login"] = False
        parts = text.split(None, 1)
        if len(parts) < 2:
            await update.message.reply_text(
                "❗ فرمت اشتباه. به این صورت بفرست:\n`username password`",
                parse_mode="Markdown",
                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 برگشت", callback_data="dl_instagram")]])
            )
            return
        username, password = parts[0], parts[1]
        save_json(IG_LOGIN_FILE, {"username": username, "password": password})
        msg = await update.message.reply_text("⏳ در حال ورود به اینستاگرام...")
        try:
            import instaloader
            L = instaloader.Instaloader()
            L.login(username, password)
            L.save_session_to_file(IG_SESSION_FILE)
            await msg.edit_text("✅ ورود موفق. حالا می‌تونی از اینستاگرام دانلود کنی.", reply_markup=main_menu(uid))
        except Exception as e:
            await msg.edit_text(f"❌ خطا در ورود:\n`{str(e)[:200]}`", parse_mode="Markdown", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 برگشت", callback_data="dl_instagram")]]))
        return

    # ── Force channel input ──
    if context.user_data.get("awaiting_force_channel"):
        context.user_data["awaiting_force_channel"] = False
        channel = text.strip()
        if not channel.startswith("@"):
            await update.message.reply_text(
                "❗ نام کانال باید با @ شروع بشه.\nمثال: `@my_channel`",
                parse_mode="Markdown",
                reply_markup=settings_menu()
            )
            return
        set_force_channel(channel)
        await update.message.reply_text(
            f"✅ کانال `{channel}` تنظیم شد.\n"
            "کاربران برای استفاده از ربات باید عضو این کانال باشند.",
            parse_mode="Markdown",
            reply_markup=settings_menu()
        )
        return

    # ── Welcome message input ──
    if context.user_data.get("awaiting_welcome_msg"):
        context.user_data["awaiting_welcome_msg"] = False
        if text.strip().lower() == "reset":
            set_welcome_message("")
            await update.message.reply_text(
                "✅ پیغام خوش‌آمدگویی به حالت پیش‌فرض بازگشت.",
                reply_markup=advanced_settings_menu()
            )
        else:
            set_welcome_message(text)
            await update.message.reply_text(
                "✅ پیغام خوش‌آمدگویی ذخیره شد.",
                reply_markup=advanced_settings_menu()
            )
        return

    # ── Port input ──
    if context.user_data.get("awaiting_port"):
        context.user_data["awaiting_port"] = False
        if text.strip().isdigit():
            p = int(text.strip())
            if 1 <= p <= 65535:
                set_http_port(p)
                await update.message.reply_text(
                    f"✅ پورت به `{p}` تغییر یافت.\n⚠️ برای اعمال تغییر، ربات را ری‌استارت کنید.",
                    parse_mode="Markdown",
                    reply_markup=advanced_settings_menu()
                )
            else:
                await update.message.reply_text("❗ پورت باید بین ۱ تا ۶۵۵۳۵ باشد.", reply_markup=advanced_settings_menu())
        else:
            await update.message.reply_text("❗ لطفاً یک عدد معتبر بفرستید.", reply_markup=advanced_settings_menu())
        return

    # ── Domain input ──
    if context.user_data.get("awaiting_domain"):
        context.user_data["awaiting_domain"] = False
        if text.strip().lower() == "reset":
            set_base_url_config("")
            await update.message.reply_text(
                "✅ دامنه پاک شد. ربات از IP محلی استفاده خواهد کرد.",
                reply_markup=advanced_settings_menu()
            )
        else:
            set_base_url_config(text.strip())
            await update.message.reply_text(
                f"✅ دامنه تنظیم شد: `{text.strip()}`",
                parse_mode="Markdown",
                reply_markup=advanced_settings_menu()
            )
        return

    if is_admin(uid):
        if context.user_data.get("awaiting_broadcast"):
            context.user_data["awaiting_broadcast"] = False
            users = load_user_ids()
            sent = 0
            failed = 0
            for u in users:
                try:
                    chat_id = u["id"] if isinstance(u, dict) else u
                    await context.bot.send_message(chat_id=chat_id, text=f"📢 **پیام همگانی:**\n\n{text}", parse_mode="Markdown")
                    sent += 1
                except:
                    failed += 1
            await update.message.reply_text(f"✅ ارسال شد به {sent} کاربر.\n❌ ناموفق: {failed}", reply_markup=main_menu(uid))
            return

        if context.user_data.get("awaiting_add_admin"):
            context.user_data["awaiting_add_admin"] = False
            try:
                new_admin = int(text)
            except ValueError:
                await update.message.reply_text("❗ آیدی نامعتبر است.", reply_markup=users_menu())
                return
            admins = load_admins()
            if new_admin in admins:
                await update.message.reply_text("⚠️ این کاربر قبلاً ادمین است.", reply_markup=users_menu())
                return
            admins.append(new_admin)
            save_admins(admins)
            await update.message.reply_text(f"✅ کاربر `{new_admin}` به ادمین‌ها اضافه شد.", parse_mode="Markdown", reply_markup=users_menu())
            return

        if context.user_data.get("awaiting_remove_admin"):
            context.user_data["awaiting_remove_admin"] = False
            try:
                remove_id = int(text)
            except ValueError:
                await update.message.reply_text("❗ آیدی نامعتبر است.", reply_markup=users_menu())
                return
            admins = load_admins()
            if remove_id not in admins:
                await update.message.reply_text("⚠️ این کاربر ادمین نیست.", reply_markup=users_menu())
                return
            if remove_id == uid:
                await update.message.reply_text("⚠️ نمی‌توانید خودتان را حذف کنید.", reply_markup=users_menu())
                return
            admins.remove(remove_id)
            save_admins(admins)
            await update.message.reply_text(f"✅ کاربر `{remove_id}` از ادمین‌ها حذف شد.", parse_mode="Markdown", reply_markup=users_menu())
            return

    # ── GSMArena search (from menu) ──
    if context.user_data.get("awaiting_gsm"):
        context.user_data["awaiting_gsm"] = False
        msg = await update.message.reply_text("⏳ در حال جستجو...")
        data = await asyncio.to_thread(gsmarena_search, text.strip())
        if data and data.get("specs"):
            formatted = format_gsm_specs(data)
            if data.get("img_url"):
                try:
                    await update.message.reply_photo(
                        photo=data["img_url"],
                        caption=formatted,
                        parse_mode="Markdown",
                        reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 برگشت", callback_data="section_gsm")]])
                    )
                    await msg.delete()
                    return
                except:
                    pass
            await msg.edit_text(formatted, parse_mode="Markdown", disable_web_page_preview=True,
                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 برگشت", callback_data="section_gsm")]]))
        else:
            await msg.edit_text(
                "❌ گوشی مورد نظر پیدا نشد.\n"
                "اسم مدل رو دقیق‌تر وارد کن.\n\n"
                "مثال: `Samsung Galaxy S24`\n"
                "یا کد مدل: `SM-S928B`",
                parse_mode="Markdown",
                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 برگشت", callback_data="section_gsm")]])
            )
        return

    # ── Calendar converter ──
    cal_mode = context.user_data.get("awaiting_calendar")
    if cal_mode:
        context.user_data["awaiting_calendar"] = None
        mode, example = cal_mode
        try:
            parts = text.strip().split("/")
            if len(parts) != 3:
                raise ValueError
            y, m, d = int(parts[0]), int(parts[1]), int(parts[2])
            if mode == "shamsi_to_miladi":
                gy, gm, gd = shamsi_to_miladi(y, m, d)
                result = f"📅 **شمسی به میلادی**\n\n{y}/{m:02d}/{d:02d} ← {gy}/{gm:02d}/{gd:02d}"
            elif mode == "miladi_to_shamsi":
                sy, sm, sd = miladi_to_shamsi(y, m, d)
                result = f"📅 **میلادی به شمسی**\n\n{y}/{m:02d}/{d:02d} ← {sy}/{sm:02d}/{sd:02d}"
            elif mode == "shamsi_to_ghamari":
                hy, hm, hd = shamsi_to_ghamari(y, m, d)
                result = f"📅 **شمسی به قمری**\n\n{y}/{m:02d}/{d:02d} ← {hy}/{hm:02d}/{hd:02d}"
            else:
                sy, sm, sd = ghamari_to_shamsi(y, m, d)
                result = f"📅 **قمری به شمسی**\n\n{y}/{m:02d}/{d:02d} ← {sy}/{sm:02d}/{sd:02d}"
        except:
            result = f"❌ فرمت تاریخ نامعتبر.\nلطفاً به فرمت `سال/ماه/روز` وارد کنید.\nمثال: `{example}`"
        await update.message.reply_text(result, parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 برگشت", callback_data="tools_calendar")]]))
        return

    # ── Admin URL download ──
    if context.user_data.get("awaiting_admin_dl"):
        context.user_data["awaiting_admin_dl"] = False
        url = text.strip()
        if not url.startswith("http://") and not url.startswith("https://"):
            await update.message.reply_text("❗ لطفاً یک لینک معتبر با `http://` یا `https://` وارد کنید.",
                parse_mode="Markdown",
                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 برگشت", callback_data="section_filehost")]]))
            return

        import requests as req_lib
        import concurrent.futures

        dl_dir = DOWNLOAD_DIR / "admin_dl" / str(uid)
        dl_dir.mkdir(parents=True, exist_ok=True)
        file_name = url.split("/")[-1].split("?")[0] or f"file_{int(time.time())}"
        file_path = dl_dir / file_name
        msg = await update.message.reply_text("⏳ شروع دانلود...")

        prog = {"downloaded": 0, "total": 0, "done": False, "error": None, "resumed": False}

        def worker():
            try:
                resume_pos = 0
                if file_path.exists():
                    resume_pos = file_path.stat().st_size
                hdrs = {"User-Agent": "Mozilla/5.0"}
                if resume_pos > 0:
                    hdrs["Range"] = f"bytes={resume_pos}-"
                r = req_lib.get(url, headers=hdrs, stream=True, timeout=300, allow_redirects=True)
                r.raise_for_status()
                total = int(r.headers.get("Content-Length", 0))
                if resume_pos > 0:
                    if r.status_code == 206:
                        total += resume_pos
                        prog["resumed"] = True
                    else:
                        resume_pos = 0
                        total += 0
                prog["total"] = total
                if total > MAX_TG_FILE:
                    prog["error"] = f"حجم فایل بیشتر از ۱۱ گیگابایت است ({total/1024/1024/1024:.1f}GB)"
                    prog["done"] = True
                    return
                mode = "ab" if resume_pos > 0 and r.status_code == 206 else "wb"
                if mode == "wb":
                    resume_pos = 0
                with open(file_path, mode) as f:
                    for chunk in r.iter_content(chunk_size=262144):
                        if chunk:
                            f.write(chunk)
                            prog["downloaded"] += len(chunk)
                prog["done"] = True
            except Exception as e:
                prog["error"] = str(e)[:300]
                prog["done"] = True

        loop = asyncio.get_running_loop()
        executor = concurrent.futures.ThreadPoolExecutor(max_workers=1)
        future = loop.run_in_executor(executor, worker)

        last_pct = -1
        while not prog["done"]:
            await asyncio.sleep(2)
            total = prog["total"]
            downloaded = prog["downloaded"]
            if total > 0:
                pct = min(int(downloaded * 100 / total), 99)
                if pct != last_pct:
                    last_pct = pct
                    dl_mb = downloaded / 1024 / 1024
                    total_mb = total / 1024 / 1024
                    bar = "▓" * (pct // 5) + "░" * (20 - pct // 5)
                    try:
                        await msg.edit_text(
                            f"📥 **در حال دانلود...** `{pct}%`\n"
                            f"`{bar}`\n"
                            f"📦 `{dl_mb:.1f} MB / {total_mb:.1f} MB`"
                        )
                    except:
                        pass

        await future
        if prog["error"]:
            await msg.edit_text(f"❌ خطا: `{prog['error'][:200]}`",
                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 برگشت", callback_data="section_filehost")]]))
            return

        file_size = file_path.stat().st_size
        if file_size > MAX_TG_FILE:
            file_path.unlink()
            await msg.edit_text("❌ حجم فایل بیشتر از ۱۱ گیگابایت است.",
                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 برگشت", callback_data="section_filehost")]]))
            return

        try:
            relative_path = file_path.resolve().relative_to(Path.cwd().resolve())
        except ValueError:
            relative_path = file_path.relative_to(Path.cwd())
        direct_url = make_direct_url(relative_path)

        await msg.delete()
        txt = f"✅ **دانلود کامل شد**\n\n📁 نام: `{file_name}`\n📦 حجم: `{file_size / 1024 / 1024:.1f} MB`\n\n🔗 **لینک دانلود:**\n`{direct_url}`"
        if prog["resumed"]:
            txt = f"🔄 **ادامه دانلود از سر گرفته شد**\n\n{txt}"
        await update.message.reply_text(txt, parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔗 باز کردن لینک", url=direct_url)]]))
        return

    # ── QR code ──
    if context.user_data.get("awaiting_qr"):
        context.user_data["awaiting_qr"] = False
        qr_path = DOWNLOAD_DIR / "qrcodes" / f"{uid}_{int(time.time())}.png"
        qr_path.parent.mkdir(parents=True, exist_ok=True)
        msg = await update.message.reply_text("⏳ در حال ساخت QR کد...")
        try:
            await asyncio.to_thread(generate_qr, text, str(qr_path))
            await msg.delete()
            with open(qr_path, "rb") as f:
                await update.message.reply_photo(
                    photo=f,
                    caption=f"✅ QR کد ساخته شد.\n🔗 محتوا: `{text[:100]}`",
                    parse_mode="Markdown",
                    reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 برگشت", callback_data="section_tools")]])
                )
        except Exception as e:
            await msg.edit_text(f"❌ خطا: `{str(e)[:200]}`", parse_mode="Markdown")
        finally:
            try:
                qr_path.unlink()
            except:
                pass
        return

    # ── URL shortener ──
    if context.user_data.get("awaiting_shorten"):
        context.user_data["awaiting_shorten"] = False
        if not text.startswith("http://") and not text.startswith("https://"):
            await update.message.reply_text(
                "❗ لطفاً یک لینک معتبر با `http://` یا `https://` ارسال کنید.",
                parse_mode="Markdown",
                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 برگشت", callback_data="section_tools")]])
            )
            return
        try:
            code = await asyncio.to_thread(create_short_url, text)
            short_url = f"{get_base_url()}/s/{code}"
            await update.message.reply_text(
                f"✅ **لینک کوتاه ساخته شد:**\n\n"
                f"`{short_url}`\n\n"
                f"🔗 لینک اصلی:\n`{text}`",
                parse_mode="Markdown",
                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔗 باز کردن لینک کوتاه", url=short_url)]])
            )
        except Exception as e:
            await update.message.reply_text(f"❌ خطا: `{str(e)[:200]}`", parse_mode="Markdown")
        return

    # ── Download (with auto-detect) ──
    mode = context.user_data.get("download_mode")
    if mode:
        context.user_data["download_mode"] = None
    else:
        mode = detect_platform(text)

    if mode:
        # Check daily limit
        if not check_daily_limit(uid):
            limit = get_daily_limit()
            used = get_usage_today(uid)
            await update.message.reply_text(
                f"⛔ محدودیت روزانه: {limit} تا\n"
                f"شما امروز {used} بار استفاده کردید.\n"
                f"فردا دوباره امتحان کنید."
            )
            return

        msg = await update.message.reply_text("⏳ در حال دریافت اطلاعات... لطفاً صبر کنید.")

        try:
            # Create a unique directory for this download
            dl_dir = DOWNLOAD_DIR / str(uid) / str(int(time.time()))
            dl_dir.mkdir(parents=True, exist_ok=True)

            if mode == "instagram":
                filepath = download_instagram(text, dl_dir)
            else:
                filepath = download_video(text, dl_dir, mode)
            file_size = filepath.stat().st_size
            file_name = filepath.name

            await msg.edit_text(f"✅ دانلود完成: {file_name}\n📦 حجم: {file_size / 1024 / 1024:.1f} MB")

            # Delete progress message
            try:
                await msg.delete()
            except:
                pass

            if file_size <= MAX_TELEGRAM_UPLOAD:
                try:
                    with open(filepath, "rb") as f:
                        await update.message.reply_video(
                            video=f,
                            caption=f"📥 {file_name}",
                            read_timeout=300,
                            write_timeout=300,
                        )
                except:
                    pass

            # Increment usage counter
            increment_usage(uid)

            # Auto-delete user request if enabled
            if get_auto_delete():
                try:
                    await update.message.delete()
                except:
                    pass

            # Provide direct download link
            relative_path = filepath.absolute().relative_to(Path.cwd())
            direct_url = make_direct_url(relative_path)
            await update.message.reply_text(
                f"🔗 **لینک دانلود مستقیم:**\n`{direct_url}`\n\n"
                f"⏰ این لینک تا {get_file_expiry() // 3600} ساعت معتبر است.",
                parse_mode="Markdown"
            )

        except subprocess.TimeoutExpired:
            try:
                await msg.edit_text("⏰ زمان دانلود به پایان رسید. لینک را بررسی کنید.")
            except:
                await update.message.reply_text("⏰ زمان دانلود به پایان رسید. لینک را بررسی کنید.")
        except Exception as e:
            err_text = str(e)[:300]
            if mode == "instagram" and "empty media" in err_text:
                err_text += (
                    "\n\n💡 برای اینستاگرام باید لاگین باشی.\n"
                    "دستور /cookies رو بزن تا راهنما رو ببینی."
                )
            try:
                await msg.edit_text(f"❌ خطا در دانلود:\n`{err_text}`", parse_mode="Markdown")
            except:
                await update.message.reply_text(f"❌ خطا در دانلود:\n`{err_text}`", parse_mode="Markdown")
        finally:
            cleanup_old_files()

        return

    # Unknown text
    # ── Auto-detect: text → GSMArena search ──
    if text.strip():
        msg = await update.message.reply_text("⏳ در حال جستجو...")
        data = await asyncio.to_thread(gsmarena_search, text.strip())
        if data and data.get("specs"):
            formatted = format_gsm_specs(data)
            if data.get("img_url"):
                try:
                    await update.message.reply_photo(
                        photo=data["img_url"],
                        caption=formatted,
                        parse_mode="Markdown",
                        reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 برگشت", callback_data="back_main")]])
                    )
                    await msg.delete()
                    return
                except:
                    pass
            await msg.edit_text(formatted, parse_mode="Markdown", disable_web_page_preview=True,
                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 برگشت", callback_data="back_main")]]))
        else:
            await msg.edit_text(
                "❌ گوشی مورد نظر پیدا نشد.\n"
                "اسم مدل رو دقیق‌تر وارد کن.\n\n"
                "مثال: `Samsung Galaxy S24`\n"
                "یا کد مدل: `SM-S928B`",
                parse_mode="Markdown",
                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 برگشت", callback_data="back_main")]])
            )
        return

    # Fallback
    await update.message.reply_text(
        "❓ دستور نامعتبر. از /start برای نمایش منو استفاده کنید."
    )

# ─── Utils ──────────────────────────────────────────────────────────────
def esc_md(text):
    """Escape Markdown special characters for parse_mode='Markdown'."""
    return text.replace("_", "\\_").replace("*", "\\*").replace("[", "\\[").replace("`", "\\`")


def gsmarena_search(query):
    """Search GSMArena via DuckDuckGo and return phone info dict or None."""
    from bs4 import BeautifulSoup
    from curl_cffi import requests
    import urllib.parse, re
    try:
        r = requests.get("https://lite.duckduckgo.com/lite/",
            params={"q": f"{query} gsmarena"},
            impersonate="chrome124", timeout=20)
        r.raise_for_status()
        soup = BeautifulSoup(r.text, "lxml")
        phone_url = None
        phone_name = None
        for a in soup.select("a"):
            href = a.get("href", "")
            text = a.text.strip()
            if "uddg=" in href:
                m = re.search(r"uddg=([^&]+)", href)
                if m:
                    decoded = urllib.parse.unquote(m.group(1))
                    if "gsmarena.com" in decoded and re.search(r"-\d+\.php", decoded):
                        # Skip reviews, comparisons, and int'l domains
                        if not any(skip in decoded for skip in ["review", "compare", ".com.", ".com.bd", ".com.in", ".co.uk", ".ng"]):
                            phone_url = decoded
                            phone_name = text
                            break
        if not phone_url:
            return None

        # Fetch detail page
        r2 = requests.get(phone_url, impersonate="chrome124", timeout=20)
        r2.raise_for_status()
        soup2 = BeautifulSoup(r2.text, "lxml")
        # Image
        img = soup2.select_one(".specs-photo-main img")
        img_url = img["src"] if img else None
        # Specs table (id='specs-list' contains multiple <table> elements)
        specs = {}
        for table in soup2.select("#specs-list table"):
            cat_el = table.select_one("th")
            cat_name = cat_el.text.strip() if cat_el else ""
            specs[cat_name] = {}
            for row in table.select("tr")[1:]:  # skip header row
                ttl = row.select_one("td.ttl")
                nfo = row.select_one("td.nfo")
                if ttl and nfo:
                    key = ttl.text.strip().rstrip(":")
                    val = nfo.text.strip()
                    specs[cat_name][key] = val
        return {"name": phone_name, "url": phone_url, "img_url": img_url, "specs": specs}
    except:
        return None


def format_gsm_specs(data):
    """Format phone data into a readable text."""
    hide = {"Launch", "Body", "Comms", "Our Tests"}
    text = f"📱 **{esc_md(data['name'])}**\n\n"
    for cat, items in data["specs"].items():
        if cat in hide:
            continue
        text += f"**{esc_md(cat)}**\n"
        count = 0
        for k, v in list(items.items())[:8]:
            text += f"▫️ {esc_md(k)}: {esc_md(v)}\n"
            count += 1
        if count:
            text += "\n"
    text += f"🔗 [مشاهده در GSMArena]({data['url']})"
    return text


def is_url(text):
    youtube = re.search(r"(youtube\.com|youtu\.be)", text)
    instagram = re.search(r"(instagram\.com|instagr\.am)", text)
    tiktok = re.search(r"(tiktok\.com|vm\.tiktok)", text)
    if youtube:
        return "یوتیوب"
    if instagram:
        return "اینستاگرام"
    if tiktok:
        return "تیک‌تاک"
    return None


# ─── Image processing ──────────────────────────────────────────────────
def upscale_image(input_path, output_path, scale=2):
    """Upscale image using Pillow with LANCZOS resampling."""
    from PIL import Image, ImageEnhance
    img = Image.open(input_path)
    new_size = (img.width * scale, img.height * scale)
    img = img.resize(new_size, Image.LANCZOS)
    enhancer = ImageEnhance.Sharpness(img)
    img = enhancer.enhance(1.3)
    img.save(output_path, quality=95)
    return output_path


def remove_background(input_path, output_path):
    """Remove background using rembg."""
    from rembg import remove
    with open(input_path, "rb") as f:
        input_bytes = f.read()
    output_bytes = remove(input_bytes)
    with open(output_path, "wb") as f:
        f.write(output_bytes)
    return output_path


# ─── Media conversion ───────────────────────────────────────────────────
def convert_image_format(input_path, output_path, fmt):
    from PIL import Image
    img = Image.open(input_path)
    if fmt.upper() == "JPEG":
        img = img.convert("RGB")
    img.save(output_path, fmt.upper())
    return output_path


def convert_video_to_gif(input_path, output_path, fps=10):
    import subprocess
    subprocess.run(
        ["ffmpeg", "-i", str(input_path), "-vf", f"fps={fps}", str(output_path)],
        check=True, capture_output=True, timeout=120)
    return output_path


def extract_audio(input_path, output_path):
    import subprocess
    subprocess.run(
        ["ffmpeg", "-i", str(input_path), "-q:a", "0", "-map", "a", str(output_path)],
        check=True, capture_output=True, timeout=120)
    return output_path


# ─── QR code ────────────────────────────────────────────────────────────
def generate_qr(data, output_path):
    import qrcode
    img = qrcode.make(data)
    img.save(output_path)
    return output_path


# ─── URL shortener ──────────────────────────────────────────────────────
def load_short_urls():
    if SHORT_URLS_FILE.exists():
        with open(SHORT_URLS_FILE) as f:
            return json.load(f)
    return {}


def save_short_urls(data):
    with open(SHORT_URLS_FILE, "w") as f:
        json.dump(data, f, indent=2)


def create_short_url(original_url):
    import string, random
    short_urls = load_short_urls()
    code = "".join(random.choices(string.ascii_letters + string.digits, k=6))
    while code in short_urls:
        code = "".join(random.choices(string.ascii_letters + string.digits, k=6))
    short_urls[code] = original_url
    save_short_urls(short_urls)
    return code


def resolve_short_url(code):
    short_urls = load_short_urls()
    return short_urls.get(code)


# ─── Phone search by image ──────────────────────────────────────────────
def search_phone_by_image(image_path):
    """Reverse image search via DDG, return first GSMArena result."""
    import urllib.parse, re
    from curl_cffi import requests
    from bs4 import BeautifulSoup
    try:
        # Upload image to a temporary hosting and search
        with open(image_path, "rb") as f:
            r = requests.post("https://lite.duckduckgo.com/lite/",
                files={"image": f}, impersonate="chrome124", timeout=20)
        if r.status_code != 200:
            return None
        soup = BeautifulSoup(r.text, "lxml")
        for a in soup.select("a"):
            href = a.get("href", "")
            if "uddg=" in href:
                m = re.search(r"uddg=([^&]+)", href)
                if m:
                    decoded = urllib.parse.unquote(m.group(1))
                    if "gsmarena.com" in decoded and re.search(r"-\d+\.php", decoded):
                        if not any(s in decoded for s in ["review", "compare", ".com.", ".com.bd", ".com.in", ".co.uk", ".ng"]):
                            return {"name": a.text.strip(), "url": decoded}
        return None
    except:
        return None


def detect_platform(url):
    yt = re.search(r"(youtube\.com|youtu\.be)", url)
    ig = re.search(r"(instagram\.com|instagr\.am)", url)
    tt = re.search(r"(tiktok\.com|vm\.tiktok)", url)
    if yt:
        return "youtube"
    if ig:
        return "instagram"
    if tt:
        return "tiktok"
    return None


async def ig_login(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    if not is_admin(uid):
        await update.message.reply_text("⛔ دسترسی ندارید.")
        return
    if len(context.args) < 2:
        await update.message.reply_text("❗ روش: /iglogin <username> <password>")
        return
    username, password = context.args[0], " ".join(context.args[1:])
    save_json(IG_LOGIN_FILE, {"username": username, "password": password})
    # Test login
    try:
        import instaloader
        L = instaloader.Instaloader()
        L.login(username, password)
        L.save_session_to_file(IG_SESSION_FILE)
        await update.message.reply_text("✅ ورود به اینستاگرام موفقیت‌آمیز بود. از این به بعد دانلود اینستا کار می‌کنه.")
    except Exception as e:
        await update.message.reply_text(f"❌ خطا در ورود:\n`{str(e)[:200]}`", parse_mode="Markdown")


async def handle_file_upload(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id

    # Bot enabled check
    if not is_bot_enabled() and not is_admin(uid):
        await update.message.reply_text("⛔ ربات موقتاً غیرفعال شده است. بعداً امتحان کنید.")
        return

    # Force join check
    if not is_admin(uid) and not await check_force_join(uid, context):
        ch = get_force_channel()
        join_msg = f"🔒 برای استفاده از ربات باید عضو کانال زیر بشی:\n\n{ch}"
        await update.message.reply_text(
            join_msg,
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔗 عضویت در کانال", url=ch)]])
        )
        return

    # Check daily limit
    if not check_daily_limit(uid):
        limit = get_daily_limit()
        used = get_usage_today(uid)
        await update.message.reply_text(
            f"⛔ محدودیت روزانه: {limit} تا\n"
            f"شما امروز {used} بار استفاده کردید.\n"
            f"فردا دوباره امتحان کنید."
        )
        return

    # ── Photo editing (upscale / remove background) ──
    photo_edit_mode = None
    if context.user_data.get("awaiting_photo_upscale"):
        photo_edit_mode = "upscale"
        context.user_data["awaiting_photo_upscale"] = False
    elif context.user_data.get("awaiting_photo_removebg"):
        photo_edit_mode = "removebg"
        context.user_data["awaiting_photo_removebg"] = False

    if photo_edit_mode and update.message.photo:
        msg = await update.message.reply_text("⏳ در حال پردازش عکس...")
        try:
            photo = update.message.photo[-1]
            file = await photo.get_file()
            dl_dir = DOWNLOAD_DIR / "edits" / str(uid)
            dl_dir.mkdir(parents=True, exist_ok=True)
            input_path = dl_dir / f"input_{photo.file_id}.jpg"
            await file.download_to_drive(input_path)

            if photo_edit_mode == "upscale":
                output_path = dl_dir / f"upscaled_{photo.file_id}.png"
                await asyncio.to_thread(upscale_image, str(input_path), str(output_path))
                label = "✨ افزایش کیفیت"
            else:
                output_path = dl_dir / f"nobg_{photo.file_id}.png"
                await asyncio.to_thread(remove_background, str(input_path), str(output_path))
                label = "🎨 حذف بکگراند"

            await msg.delete()
            with open(output_path, "rb") as f:
                await update.message.reply_photo(
                    photo=f,
                    caption=f"✅ {label} انجام شد.",
                    reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 برگشت", callback_data="section_photoedit")]])
                )

            # Cleanup
            try:
                input_path.unlink()
                output_path.unlink()
            except:
                pass
        except Exception as e:
            try:
                await msg.edit_text(f"❌ خطا در پردازش عکس:\n`{str(e)[:300]}`", parse_mode="Markdown")
            except:
                await update.message.reply_text(f"❌ خطا در پردازش عکس:\n`{str(e)[:300]}`", parse_mode="Markdown")
        return

    # ── Media conversion handler ──
    convert_mode = context.user_data.get("awaiting_convert")
    if convert_mode:
        context.user_data["awaiting_convert"] = None
        media_types = ("photo", "video", "document", "audio", "voice")
        has_media = any(getattr(update.message, mt, None) for mt in media_types)
        if not has_media:
            await update.message.reply_text("❗ لطفاً یک فایل ارسال کنید.", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 برگشت", callback_data="tools_convert")]]))
            return

        msg = await update.message.reply_text("⏳ در حال تبدیل...")
        try:
            dl_dir = DOWNLOAD_DIR / "converted" / str(uid)
            dl_dir.mkdir(parents=True, exist_ok=True)
            timestamp = str(int(time.time()))

            if convert_mode == "image":
                if not update.message.photo and not (update.message.document and update.message.document.mime_type and update.message.document.mime_type.startswith("image/")):
                    await msg.edit_text("❗ لطفاً یک عکس ارسال کنید.", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 برگشت", callback_data="tools_convert")]]))
                    return
                if update.message.photo:
                    photo = update.message.photo[-1]
                    file = await photo.get_file()
                    input_ext = ".jpg"
                else:
                    file = await update.message.document.get_file()
                    input_ext = Path(file.file_path).suffix or ".jpg"
                input_path = dl_dir / f"input_{timestamp}{input_ext}"
                await file.download_to_drive(input_path)
                # Ask for target format
                keyboard = [
                    [InlineKeyboardButton("JPEG", callback_data="conv_img_jpeg"),
                     InlineKeyboardButton("PNG", callback_data="conv_img_png"),
                     InlineKeyboardButton("WebP", callback_data="conv_img_webp")],
                    [InlineKeyboardButton("🔙 برگشت", callback_data="tools_convert")],
                ]
                await msg.edit_text("🖼 **انتخاب فرمت مقصد:**", parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(keyboard))
                context.user_data["awaiting_convert_file"] = str(input_path)
                return

            elif convert_mode == "video_to_gif":
                if not update.message.video:
                    await msg.edit_text("❗ لطفاً یک ویدیو ارسال کنید.", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 برگشت", callback_data="tools_convert")]]))
                    return
                video = update.message.video
                file = await video.get_file()
                input_path = dl_dir / f"input_{timestamp}.mp4"
                await file.download_to_drive(input_path)
                output_path = dl_dir / f"output_{timestamp}.gif"
                await asyncio.to_thread(convert_video_to_gif, str(input_path), str(output_path))
                await msg.delete()
                with open(output_path, "rb") as f:
                    await update.message.reply_document(
                        document=f, filename=f"converted_{timestamp}.gif",
                        caption="✅ تبدیل به GIF انجام شد.",
                        reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 برگشت", callback_data="tools_convert")]])
                    )
                try:
                    input_path.unlink(); output_path.unlink()
                except: pass
                return

            elif convert_mode == "extract_audio":
                if not update.message.video:
                    await msg.edit_text("❗ لطفاً یک ویدیو ارسال کنید.", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 برگشت", callback_data="tools_convert")]]))
                    return
                video = update.message.video
                file = await video.get_file()
                input_path = dl_dir / f"input_{timestamp}.mp4"
                await file.download_to_drive(input_path)
                output_path = dl_dir / f"output_{timestamp}.mp3"
                await asyncio.to_thread(extract_audio, str(input_path), str(output_path))
                file_size = output_path.stat().st_size
                await msg.edit_text(f"✅ استخراج صوت انجام شد.\n📦 حجم: {file_size / 1024 / 1024:.1f} MB")
                if file_size <= MAX_TELEGRAM_UPLOAD:
                    with open(output_path, "rb") as f:
                        await update.message.reply_audio(
                            audio=f, title=f"audio_{timestamp}",
                            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 برگشت", callback_data="tools_convert")]])
                        )
                else:
                    relative_path = output_path.absolute().relative_to(Path.cwd())
                    direct_url = make_direct_url(relative_path)
                    await update.message.reply_text(f"🔗 لینک دانلود:\n`{direct_url}`\n⏰ معتبر تا {get_file_expiry() // 3600} ساعت.",
                        parse_mode="Markdown")
                try:
                    input_path.unlink()
                except: pass
                return

        except Exception as e:
            try:
                await msg.edit_text(f"❌ خطا:\n`{str(e)[:300]}`", parse_mode="Markdown")
            except:
                await update.message.reply_text(f"❌ خطا:\n`{str(e)[:300]}`", parse_mode="Markdown")
            return

    # ── Phone search by image (GSMArena) ──
    if context.user_data.get("awaiting_gsm_photo"):
        context.user_data["awaiting_gsm_photo"] = False
        if not update.message.photo:
            await update.message.reply_text("❗ لطفاً یک عکس ارسال کنید.", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 برگشت", callback_data="section_gsm")]]))
            return
        msg = await update.message.reply_text("⏳ در حال جستجوی گوشی با عکس...")
        try:
            photo = update.message.photo[-1]
            file = await photo.get_file()
            dl_dir = DOWNLOAD_DIR / "gsm_search" / str(uid)
            dl_dir.mkdir(parents=True, exist_ok=True)
            input_path = dl_dir / f"search_{photo.file_id}.jpg"
            await file.download_to_drive(input_path)
            result = await asyncio.to_thread(search_phone_by_image, str(input_path))
            try:
                input_path.unlink()
            except: pass
            if result and result.get("url"):
                # Fetch phone details
                data = await asyncio.to_thread(lambda: gsmarena_search(result["name"]))
                if data and data.get("specs"):
                    formatted = format_gsm_specs(data)
                    await msg.delete()
                    if data.get("img_url"):
                        try:
                            await update.message.reply_photo(photo=data["img_url"], caption=formatted,
                                parse_mode="Markdown", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 برگشت", callback_data="section_gsm")]]))
                            return
                        except:
                            pass
                    await update.message.reply_text(formatted, parse_mode="Markdown", disable_web_page_preview=True,
                        reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 برگشت", callback_data="section_gsm")]]))
                else:
                    await msg.edit_text(f"🔍 گوشی یافت شد: {result['name']}\n{result['url']}",
                        disable_web_page_preview=True, reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 برگشت", callback_data="section_gsm")]]))
            else:
                await msg.edit_text("❌ گوشی در عکس تشخیص داده نشد.\nاز زاویه مناسب‌تر عکس بگیرید.",
                    reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 برگشت", callback_data="section_gsm")]]))
        except Exception as e:
            try:
                await msg.edit_text(f"❌ خطا:\n`{str(e)[:300]}`", parse_mode="Markdown")
            except:
                await update.message.reply_text(f"❌ خطا:\n`{str(e)[:300]}`", parse_mode="Markdown")
        return

    msg = await update.message.reply_text("⏳ در حال آپلود...")

    try:
        # Get file
        file = None
        file_name = None
        if update.message.document:
            file = await update.message.document.get_file()
            file_name = update.message.document.file_name
        elif update.message.video:
            file = await update.message.video.get_file()
            file_name = f"video_{update.message.video.file_id}.mp4"
        elif update.message.audio:
            file = await update.message.audio.get_file()
            file_name = update.message.audio.file_name or f"audio_{update.message.audio.file_id}.mp3"
        elif update.message.photo:
            photo = update.message.photo[-1]
            file = await photo.get_file()
            file_name = f"photo_{photo.file_id}.jpg"
        elif update.message.voice:
            file = await update.message.voice.get_file()
            file_name = f"voice_{update.message.voice.file_id}.ogg"
        else:
            await msg.edit_text("❌ این نوع فایل پشتیبانی نمی‌شه.")
            return

        # Save file
        dl_dir = DOWNLOAD_DIR / "uploads" / str(uid)
        dl_dir.mkdir(parents=True, exist_ok=True)
        file_path = dl_dir / file_name
        await file.download_to_drive(file_path)

        # Increment usage counter
        increment_usage(uid)

        # Auto-delete user request if enabled
        if get_auto_delete():
            try:
                await update.message.delete()
            except:
                pass

        # Generate direct link
        relative_path = file_path.absolute().relative_to(Path.cwd())
        direct_url = make_direct_url(relative_path)

        await msg.edit_text(
            f"✅ **فایل آپلود شد**\n\n"
            f"📁 نام: `{file_name}`\n"
            f"📦 حجم: `{file_path.stat().st_size / 1024:.1f} KB`\n\n"
            f"🔗 **لینک دانلود:**\n`{direct_url}`\n\n"
            f"⏰ این لینک تا {get_file_expiry() // 3600} ساعت معتبر است.",
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔗 باز کردن لینک", url=direct_url)]])
        )

    except Exception as e:
        await msg.edit_text(f"❌ خطا: `{str(e)[:200]}`", parse_mode="Markdown")


async def set_limit_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    if not is_admin(uid):
        await update.message.reply_text("⛔ دسترسی ندارید.")
        return
    if not context.args or not context.args[0].isdigit():
        await update.message.reply_text("❗ روش: /setlimit <تعداد>\nمثال: /setlimit 5")
        return
    n = int(context.args[0])
    set_daily_limit(n)
    await update.message.reply_text(f"✅ محدودیت روزانه به {n} تا تغییر یافت.")


async def view_limit_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    limit = get_daily_limit()
    used_today = 0
    if is_admin(update.effective_user.id):
        # Show global stats
        d = load_limits()
        usage = d.get("usage", {})
        text = (
            f"📊 **محدودیت روزانه:** {limit} تا برای هر کاربر\n\n"
            f"**میزان استفاده امروز:**\n"
        )
        for uid_str, days in usage.items():
            for day, count in days.items():
                if day == time.strftime("%Y-%m-%d"):
                    text += f"👤 کاربر `{uid_str}`: {count} بار\n"
        await update.message.reply_text(text, parse_mode="Markdown")
    else:
        used = get_usage_today(uid)
        remaining = limit - used
        await update.message.reply_text(
            f"📊 محدودیت روزانه: {limit} تا\n"
            f"استفاده امروز: {used} تا\n"
            f"باقی‌مانده: {remaining} تا"
        )


async def ig_logout(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    if not is_admin(uid):
        await update.message.reply_text("⛔ دسترسی ندارید.")
        return
    if IG_SESSION_FILE.exists():
        IG_SESSION_FILE.unlink()
    if IG_LOGIN_FILE.exists():
        IG_LOGIN_FILE.unlink()
    await update.message.reply_text("✅ از اینستاگرام خارج شدید.")


async def set_cookies(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    if not is_admin(uid):
        await update.message.reply_text("⛔ دسترسی ندارید.")
        return
    await update.message.reply_text(
        "📄 فایل cookies.txt رو آپلود کن.\n\n"
        "روش تهیه:\n"
        "1. افزونه Get cookies.txt رو نصب کن\n"
        "2. برو تو instagram.com و لاگین کن\n"
        "3. کوکی‌ها رو خروجی بگیر\n"
        "4. فایل رو بفرست"
    )


async def handle_cookies_file(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    if not is_admin(uid):
        return
    file = await update.message.document.get_file()
    await file.download_to_drive(COOKIES_FILE)
    await update.message.reply_text("✅ فایل cookies.txt ذخیره شد. حالا اینستاگرام کار می‌کنه.")


def cleanup_old_files():
    """Remove files older than configured expiry time."""
    now = time.time()
    expiry = get_file_expiry()
    for f in DOWNLOAD_DIR.rglob("*"):
        if f.is_file() and now - f.stat().st_mtime > expiry:
            try:
                f.unlink()
            except OSError:
                pass
    # Remove empty directories
    for d in sorted(DOWNLOAD_DIR.rglob("*"), key=lambda x: len(str(x)), reverse=True):
        if d.is_dir():
            try:
                d.rmdir()
            except OSError:
                pass


def cleanup_loop():
    while True:
        time.sleep(CLEANUP_INTERVAL)
        cleanup_old_files()


# ─── Main ────────────────────────────────────────────────────────────────
def main():
    # Start HTTP server thread
    http_thread = threading.Thread(target=start_http_server, daemon=True)
    http_thread.start()
    # Start cleanup thread
    t = threading.Thread(target=cleanup_loop, daemon=True)
    t.start()

    app = Application.builder().token(BOT_TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("help", help_cmd))
    app.add_handler(CommandHandler("status", status_cmd))
    app.add_handler(CommandHandler("cookies", set_cookies))
    app.add_handler(CommandHandler("iglogin", ig_login))
    app.add_handler(CommandHandler("iglogout", ig_logout))
    app.add_handler(CommandHandler("setlimit", set_limit_cmd))
    app.add_handler(CommandHandler("limit", view_limit_cmd))
    app.add_handler(MessageHandler(filters.Document.FileExtension("txt"), handle_cookies_file))
    app.add_handler(MessageHandler(filters.ATTACHMENT, handle_file_upload))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))
    app.add_handler(CallbackQueryHandler(button_handler))

    print("Bot started successfully...")
    app.run_polling()


if __name__ == "__main__":
    main()
