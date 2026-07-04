# Telegram Multi-Purpose Bot

A feature-rich Telegram bot with file hosting, media downloading, currency rates, calendar conversion, photo editing, and more.

## Features

- **File Hosting** — Upload files up to 50MB via Telegram or provide a direct download URL (admin: up to 11GB) and get a direct download link
- **Media Downloader** — Download videos/audio from YouTube, Instagram, TikTok
- **Currency Rates** — Live Iranian market rates from tgju.org (USD, EUR, GBP, AED, TRY, gold, crypto including TRX)
- **Calendar Converter** — Convert between Shamsi (Jalali), Miladi (Gregorian), and Ghamari (Hijri) dates
- **Photo Editing** — Resize, crop, convert format, compress, and remove background
- **QR Code Generator** — Generate QR codes from text
- **GSMArena Search** — Search phone specifications by model name or photo
- **URL Shortener** — Shorten URLs with custom slugs

## Installation

### Prerequisites
- Linux server (Ubuntu/Debian recommended)
- Python 3.8+
- A Telegram bot token from [@BotFather](https://t.me/BotFather)

### Quick Install

```bash
bash install.sh
```

The script will prompt for your bot token and admin ID. You can also pass them as arguments:

```bash
bash install.sh YOUR_BOT_TOKEN YOUR_ADMIN_ID [PORT] [DIRECTORY]
```

What the installer does:
1. Installs system packages (Python, ffmpeg, git, etc.)
2. Creates a Python virtual environment
3. Installs all required Python libraries
4. Sets up `data/` config files
5. Creates a systemd service for auto-start
6. Opens the HTTP port in the firewall

### Manual Install

```bash
# Clone the repo
git clone https://github.com/hasan1808/telbot.git
cd telbot

# Set up virtual environment
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt

# Edit bot.py and set your bot token
# Create data/ directory with config files (see install.sh)
```

## Configuration

All config files are stored in `data/`:

| File | Purpose |
|------|---------|
| `config.json` | HTTP port, base URL |
| `admin.json` | Admin IDs, banned users |
| `users.json` | Registered users |
| `limits.json` | Daily limits per user |
| `short_urls.json` | URL shortening mappings |
| `instagram_login.json` | Instagram session credentials |

## Usage

Send `/start` to your bot on Telegram to see the main menu.

### Admin Features
- Restart bot from settings panel
- Download from URL (up to 11GB) via file host section
- User management
- Bot enable/disable toggle

## Commands & Callbacks

All interactions are handled via inline keyboards. Main sections:
- 📁 **File Host** — Upload or URL download → direct link
- 🎬 **Media Download** — YouTube, Instagram, TikTok
- 🛠 **Tools** — Photo edit, QR code, URL shortener, calendar, currency, phone search
- ⚙️ **Settings** — Admin panel

## Direct Link HTTP Server

The bot runs a built-in HTTP server on port 8585 that serves files from the working directory, providing direct download links for uploaded content.

## Dependencies

- `python-telegram-bot` — Telegram Bot API
- `yt-dlp` — YouTube/Instagram/TikTok downloads
- `instaloader` — Instagram content
- `beautifulsoup4` + `lxml` — Web scraping (tgju.org)
- `Pillow` — Image processing
- `rembg` — Background removal
- `qrcode` — QR code generation
- `jdatetime` — Jalali date conversion
- `hijridate` — Hijri date conversion
- `requests` — HTTP downloads
- `cloudscraper` + `curl_cffi` — Cloudflare bypass

## License

MIT
