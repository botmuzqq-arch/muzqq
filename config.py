"""⚙️ Конфигурация бота — все настройки в одном месте"""

import os
import logging
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

from dotenv import load_dotenv

load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)


def _mask_url(url: str) -> str:
    if not url:
        return url
    try:
        parts = urlsplit(url)
        if parts.password or parts.username:
            netloc = parts.hostname or ""
            if parts.port:
                netloc += f":{parts.port}"
            netloc = "***:***@" + netloc
            return urlunsplit((parts.scheme, netloc, parts.path, parts.query, parts.fragment))
        return url
    except Exception:
        return "***"


# ─── Токены ──────────────────────────────────────────────────────────────
BOT_TOKEN = os.getenv("BOT_TOKEN")
if not BOT_TOKEN:
    raise ValueError("❌ BOT_TOKEN not set! Создай .env с BOT_TOKEN=...")

ADMIN_IDS = [int(x) for x in os.getenv("ADMIN_IDS", "").split(",") if x.strip().lstrip("-").isdigit()]
if not ADMIN_IDS:
    logger.warning("⚠️ ADMIN_IDS не задан! Админ-команды недоступны.")
    ADMIN_IDS = [1003757094]  # ← вставь свой ID

# ─── Пути ───────────────────────────────────────────────────────────────────
BASE_DIR = Path(__file__).parent
DOWNLOADS_DIR = BASE_DIR / "downloads"
TEMP_DIR = BASE_DIR / "temp"
DB_PATH = BASE_DIR / "bot.db"
COOKIES_FILE = BASE_DIR / "cookies.txt"

DOWNLOADS_DIR.mkdir(exist_ok=True)
TEMP_DIR.mkdir(exist_ok=True)

# ─── Cookies ──────────────────────────────────────────────────────────────────
COOKIES_EXISTS = COOKIES_FILE.exists()
COOKIES_CONTENT = os.getenv("COOKIES", "")
if COOKIES_CONTENT and not COOKIES_EXISTS:
    try:
        COOKIES_FILE.write_text(COOKIES_CONTENT, encoding="utf-8")
        COOKIES_EXISTS = True
        logger.info(f"✅ Cookies загружены из переменной окружения в {COOKIES_FILE}")
    except Exception as e:
        logger.error(f"❌ Не удалось записать cookies: {e}")

if COOKIES_EXISTS:
    logger.info(f"✅ Файл cookies найден ({COOKIES_FILE.stat().st_size} байт)")
else:
    logger.warning("⚠️ cookies.txt не найден! YouTube может блокировать часть загрузок.")

# ─── Лимиты ───────────────────────────────────────────────────────────────────
RATE_LIMIT_REQUESTS = int(os.getenv("RATE_LIMIT_REQUESTS", "10"))
RATE_LIMIT_WINDOW = int(os.getenv("RATE_LIMIT_WINDOW", "60"))
FREE_DOWNLOADS_PER_DAY = int(os.getenv("FREE_DOWNLOADS_PER_DAY", "20"))
PREMIUM_DOWNLOADS_PER_DAY = int(os.getenv("PREMIUM_DOWNLOADS_PER_DAY", "999999"))
MAX_FILE_SIZE_MB = int(os.getenv("MAX_FILE_SIZE_MB", "50"))
MAX_FILE_SIZE_BYTES = MAX_FILE_SIZE_MB * 1024 * 1024

# ─── Кеш ────────────────────────────────────────────────────────────────────
CACHE_TTL_SEARCH = int(os.getenv("CACHE_TTL_SEARCH", "300"))
CACHE_TTL_DOWNLOAD = int(os.getenv("CACHE_TTL_DOWNLOAD", "3600"))
SEARCH_MAX_RESULTS = int(os.getenv("SEARCH_MAX_RESULTS", "5"))
USER_REGISTER_CACHE_TTL = int(os.getenv("USER_REGISTER_CACHE_TTL", "3600"))

# ─── Пулы потоков ───────────────────────────────────────────────────────────
DOWNLOAD_THREAD_WORKERS = int(os.getenv("DOWNLOAD_THREAD_WORKERS", "8"))
INFO_THREAD_WORKERS = int(os.getenv("INFO_THREAD_WORKERS", "6"))
DOWNLOAD_FILE_MAX_AGE = int(os.getenv("DOWNLOAD_FILE_MAX_AGE", str(CACHE_TTL_DOWNLOAD + 600)))

TELEGRAM_FILE_SIZE_LIMIT = 50 * 1024 * 1024

# ─── Redis ─────────────────────────────────────────────────────────────────────
REDIS_URL = os.getenv("REDIS_URL", "")
USE_REDIS = bool(REDIS_URL)
if USE_REDIS:
    logger.info(f"✅ Redis включён: {_mask_url(REDIS_URL)}")
else:
    logger.info("ℹ️ Redis не задан — использую in-memory кеш")

# ─── SQLite тюнинг ────────────────────────────────────────────────────────────
SQLITE_BUSY_TIMEOUT_MS = int(os.getenv("SQLITE_BUSY_TIMEOUT_MS", "5000"))

# ─── Общие HTTP-заголовки для yt-dlp ──────────────────────────────────────
_COMMON_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-us,en;q=0.5",
}

# ─── НАСТРОЙКИ YOUTUBE (работают в первом наборе) ──────────────────────────
# Берём из первого набора: только android клиент + skip DASH/HLS/WEBPAGE
_YOUTUBE_EXTRACTOR_ARGS = {
    "youtube": {
        "player_client": ["android"],
        "skip": ["dash", "hls", "webpage"],
    }
}

def _base_opts(**overrides) -> dict:
    opts = {
        "quiet": True,
        "no_warnings": True,
        "noplaylist": True,
        "ignoreerrors": True,
        "no_color": True,
        "retries": 10,
        "fragment_retries": 10,
        "socket_timeout": 30,
        "headers": _COMMON_HEADERS,
        "extractor_args": _YOUTUBE_EXTRACTOR_ARGS,
        # extrect_flat не задаём – для скачивания он должен быть False
    }
    if COOKIES_EXISTS:
        opts["cookiefile"] = str(COOKIES_FILE)
    opts.update(overrides)
    return opts

# ─── Опции для скачивания АУДИО (YouTube) ──────────────────────────────────
# Используем fallback, который работал в первом наборе
AUDIO_FORMAT_FALLBACKS = [
    "bestaudio[ext=m4a]/bestaudio[ext=webm]/bestaudio/best",
    "bestaudio/best",
    "best",
]

YTDLP_AUDIO_OPTS = _base_opts(
    format=AUDIO_FORMAT_FALLBACKS[0],
    outtmpl=str(DOWNLOADS_DIR / "%(id)s.%(ext)s"),
    postprocessors=[{
        "key": "FFmpegExtractAudio",
        "preferredcodec": "mp3",
        "preferredquality": "192",
    }],
)

# ─── Опции для скачивания ВИДЕО (YouTube) ──────────────────────────────────
YTDLP_VIDEO_OPTS = {
    "best": _base_opts(
        format="bestvideo[ext=mp4]+bestaudio[ext=m4a]/best[ext=mp4]/best",
        outtmpl=str(DOWNLOADS_DIR / "%(id)s_best.%(ext)s"),
        merge_output_format="mp4",
    ),
    "1080p": _base_opts(
        format="bestvideo[height<=1080][ext=mp4]+bestaudio[ext=m4a]/best[height<=1080]",
        outtmpl=str(DOWNLOADS_DIR / "%(id)s_1080p.%(ext)s"),
        merge_output_format="mp4",
    ),
    "720p": _base_opts(
        format="bestvideo[height<=720][ext=mp4]+bestaudio[ext=m4a]/best[height<=720]",
        outtmpl=str(DOWNLOADS_DIR / "%(id)s_720p.%(ext)s"),
        merge_output_format="mp4",
    ),
    "480p": _base_opts(
        format="bestvideo[height<=480][ext=mp4]+bestaudio[ext=m4a]/best[height<=480]",
        outtmpl=str(DOWNLOADS_DIR / "%(id)s_480p.%(ext)s"),
        merge_output_format="mp4",
    ),
}

# ─── Instagram / TikTok — своя простая схема (сразу "best") ──────────────────
YTDLP_REELS_OPTS = _base_opts(
    format="best",
    outtmpl=str(DOWNLOADS_DIR / "%(id)s_best.%(ext)s"),
)

# ─── Быстрые опции без скачивания (поиск / превью) ────────────────────────────
YTDLP_INFO_OPTS = _base_opts(
    skip_download=True,
    extract_flat=True,
    socket_timeout=8,
)

YTDLP_PREVIEW_OPTS = _base_opts(
    skip_download=True,
    extract_flat=False,
    socket_timeout=8,
)

# ─── Платформы ────────────────────────────────────────────────────────────────
PLATFORM_PATTERNS = {
    "youtube": ["youtube.com", "youtu.be"],
    "tiktok": ["tiktok.com", "vm.tiktok.com"],
    "instagram": ["instagram.com", "instagr.am"],
    "soundcloud": ["soundcloud.com"],
    "vimeo": ["vimeo.com"],
}

PLATFORM_EMOJIS = {
    "youtube": "▶️ YouTube",
    "tiktok": "🎵 TikTok",
    "instagram": "📸 Instagram",
    "soundcloud": "☁️ SoundCloud",
    "vimeo": "🎬 Vimeo",
}

# ─── Информация о боте ────────────────────────────────────────────────────────
BOT_NAME = "Music Bot"
BOT_DESCRIPTION = "Скачивай музыку и видео с YouTube, Instagram, TikTok"
BOT_VERSION = "3.0.0"
SUPPORT_CONTACT = os.getenv("SUPPORT_CONTACT", "@your_support")
BOT_USERNAME = os.getenv("BOT_USERNAME", "your_bot")  # без @
PREMIUM_PRICE_LABEL = os.getenv("PREMIUM_PRICE_LABEL", "200 руб/мес")

logger.info("✅ Конфигурация загружена")