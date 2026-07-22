"""⚙️ Конфигурация бота — все настройки в одном месте"""

import os
import logging
from pathlib import Path
from dotenv import load_dotenv

# Загружаем .env файл
load_dotenv()

# Настройка логирования
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s"
)
logger = logging.getLogger(__name__)

# ─── Токены (ТОЛЬКО из переменных окружения!) ──────────────────────────────
BOT_TOKEN = os.getenv("BOT_TOKEN")
if not BOT_TOKEN:
    raise ValueError("❌ BOT_TOKEN not set! Create .env file with BOT_TOKEN=your_token")

ADMIN_IDS = list(map(int, os.getenv("ADMIN_IDS", "0").split(",")))
if not ADMIN_IDS or ADMIN_IDS == [0]:
    logger.warning("⚠️ No ADMIN_IDS set! Admin commands won't work.")

# ─── Пути ─────────────────────────────────────────────────────────────────────
BASE_DIR = Path(__file__).parent
DOWNLOADS_DIR = BASE_DIR / "downloads"
TEMP_DIR = BASE_DIR / "temp"
DB_PATH = BASE_DIR / "bot.db"
COOKIES_FILE = BASE_DIR / "cookies.txt"
LOG_FILE = BASE_DIR / "bot.log"

# Создаем папки
DOWNLOADS_DIR.mkdir(exist_ok=True)
TEMP_DIR.mkdir(exist_ok=True)

# ─── Проверка cookies.txt ──────────────────────────────────────────────────
COOKIES_EXISTS = COOKIES_FILE.exists()
if COOKIES_EXISTS:
    logger.info(f"✅ Cookies file found: {COOKIES_FILE}")
    logger.info(f"📁 Size: {COOKIES_FILE.stat().st_size} bytes")
else:
    logger.warning("⚠️ Cookies file NOT found! YouTube may block downloads.")

# ─── Лимиты ───────────────────────────────────────────────────────────────────
RATE_LIMIT_REQUESTS = int(os.getenv("RATE_LIMIT_REQUESTS", "10"))
RATE_LIMIT_WINDOW = int(os.getenv("RATE_LIMIT_WINDOW", "60"))
FREE_DOWNLOADS_PER_DAY = int(os.getenv("FREE_DOWNLOADS_PER_DAY", "20"))
PREMIUM_DOWNLOADS_PER_DAY = int(os.getenv("PREMIUM_DOWNLOADS_PER_DAY", "999"))
MAX_FILE_SIZE_MB = int(os.getenv("MAX_FILE_SIZE_MB", "50"))
MAX_FILE_SIZE_BYTES = MAX_FILE_SIZE_MB * 1024 * 1024

# ─── Кеш ──────────────────────────────────────────────────────────────────────
CACHE_TTL_SEARCH = int(os.getenv("CACHE_TTL_SEARCH", "300"))       # 5 минут
CACHE_TTL_DOWNLOAD = int(os.getenv("CACHE_TTL_DOWNLOAD", "3600"))  # 1 час
SEARCH_MAX_RESULTS = int(os.getenv("SEARCH_MAX_RESULTS", "5"))     # не больше 5 — быстрее рендер

# ─── Скачивание / потоки ──────────────────────────────────────────────────────
DOWNLOAD_THREAD_WORKERS = int(os.getenv("DOWNLOAD_THREAD_WORKERS", "4"))
# Сколько хранить файлы на диске перед фоновой очисткой (сек). Должно быть >= CACHE_TTL_DOWNLOAD.
DOWNLOAD_FILE_MAX_AGE = int(os.getenv("DOWNLOAD_FILE_MAX_AGE", str(CACHE_TTL_DOWNLOAD + 600)))

# ─── Telegram API ─────────────────────────────────────────────────────────────
TELEGRAM_FILE_SIZE_LIMIT = 50 * 1024 * 1024

# ─── Redis (опционально) ─────────────────────────────────────────────────────
REDIS_URL = os.getenv("REDIS_URL", "")
USE_REDIS = bool(REDIS_URL)

if USE_REDIS:
    logger.info(f"✅ Redis enabled: {REDIS_URL}")
else:
    logger.info("ℹ️ Using in-memory cache (Redis not configured)")

# ─── yt-dlp НАСТРОЙКИ ────────────────────────────────────────────────────────
# 🔥 player_client=["android"] — самый быстрый клиент (не требует webpage/js player).
# Без sleep_interval, без лишних retries — экономим каждую секунду.

YDL_COMMON_OPTS = {
    "quiet": True,
    "no_warnings": True,
    "noplaylist": True,
    "extract_flat": True,
    "retries": 2,
    "fragment_retries": 2,
    "socket_timeout": 10,
    "skip_download": True,
    "sleep_interval_requests": 0,
    "sleep_interval": 0,
    "max_sleep_interval": 0,
    "ignoreerrors": True,
    "no_color": True,
    "headers": {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
    },
    "extractor_args": {
        "youtube": {
            "player_client": ["android"],
            "skip": ["dash", "hls", "webpage"],
        }
    },
}

# Добавляем cookies если есть
if COOKIES_EXISTS:
    YDL_COMMON_OPTS["cookiefile"] = str(COOKIES_FILE)

# ─── Опции для скачивания АУДИО ───────────────────────────────────────────────
# 🔥 Без постпроцессоров — конвертация делается ОТДЕЛЬНО и только если реально нужна.
# 🔥 outtmpl строго по %(id)s — чтобы потом искать файл по video_id, а не по времени.
YTDLP_AUDIO_OPTS = {
    "quiet": True,
    "no_warnings": True,
    "noplaylist": True,
    "retries": 2,
    "fragment_retries": 2,
    "socket_timeout": 10,
    "sleep_interval_requests": 0,
    "sleep_interval": 0,
    "max_sleep_interval": 0,
    "no_color": True,
    "format": "bestaudio[ext=m4a]/bestaudio[ext=webm]/bestaudio/best",
    "outtmpl": str(DOWNLOADS_DIR / "%(id)s.%(ext)s"),
    "headers": YDL_COMMON_OPTS["headers"],
    "extractor_args": {
        "youtube": {
            "player_client": ["android"],
            "skip": ["dash", "hls"],
        }
    },
}
if COOKIES_EXISTS:
    YTDLP_AUDIO_OPTS["cookiefile"] = str(COOKIES_FILE)

# Цепочка запасных форматов на случай, если основной недоступен (гео-блок, DRM и т.д.)
AUDIO_FORMAT_FALLBACKS = [
    "bestaudio[ext=m4a]/bestaudio[ext=webm]/bestaudio/best",
    "bestaudio/best",
    "best",
]

# ─── Опции для скачивания ВИДЕО (разные качества) ─────────────────────────────
YTDLP_VIDEO_OPTS = {
    "best": {
        **YDL_COMMON_OPTS,
        "skip_download": False,
        "extract_flat": False,
        "format": "bestvideo[ext=mp4]+bestaudio[ext=m4a]/best[ext=mp4]/best",
        "outtmpl": str(DOWNLOADS_DIR / "%(id)s_best.%(ext)s"),
        "merge_output_format": "mp4",
    },
    "1080p": {
        **YDL_COMMON_OPTS,
        "skip_download": False,
        "extract_flat": False,
        "format": "bestvideo[height<=1080][ext=mp4]+bestaudio[ext=m4a]/best[height<=1080]",
        "outtmpl": str(DOWNLOADS_DIR / "%(id)s_1080p.%(ext)s"),
        "merge_output_format": "mp4",
    },
    "720p": {
        **YDL_COMMON_OPTS,
        "skip_download": False,
        "extract_flat": False,
        "format": "bestvideo[height<=720][ext=mp4]+bestaudio[ext=m4a]/best[height<=720]",
        "outtmpl": str(DOWNLOADS_DIR / "%(id)s_720p.%(ext)s"),
        "merge_output_format": "mp4",
    },
    "480p": {
        **YDL_COMMON_OPTS,
        "skip_download": False,
        "extract_flat": False,
        "format": "bestvideo[height<=480][ext=mp4]+bestaudio[ext=m4a]/best[height<=480]",
        "outtmpl": str(DOWNLOADS_DIR / "%(id)s_480p.%(ext)s"),
        "merge_output_format": "mp4",
    },
}

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

# ─── Instagram (опционально) ─────────────────────────────────────────────────
IG_USERNAME = os.getenv("IG_USERNAME", "")
IG_PASSWORD = os.getenv("IG_PASSWORD", "")

logger.info("✅ Config loaded successfully!")
