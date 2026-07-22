"""
📥 Сервис скачивания — yt-dlp обёртка

Ключевые исправления:
  • Скачивание идёт в ThreadPoolExecutor — бот НЕ блокируется.
  • Файл ищется строго по video_id (а не по времени изменения) — надёжно
    даже при нескольких параллельных скачиваниях.
  • Папка downloads больше НЕ чистится целиком перед каждым скачиванием —
    это ломало параллельные загрузки других пользователей.
  • Постпроцессоры (конвертация в mp3) отключены на этапе скачивания —
    отправляем в Telegram оригинальный m4a/webm, конвертируем отдельно
    только если формат совсем не поддерживается.
  • Прогресс скачивания передаётся в бот через run_coroutine_threadsafe,
    т.к. progress_hook выполняется в отдельном потоке, а не в event loop.
"""

import asyncio
import logging
import time
import re
import subprocess
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor
from typing import Optional, Callable, Awaitable, Union

import yt_dlp

from config import (
    AUDIO_FORMAT_FALLBACKS,
    YTDLP_AUDIO_OPTS,
    YTDLP_VIDEO_OPTS,
    DOWNLOADS_DIR,
    CACHE_TTL_DOWNLOAD,
    DOWNLOAD_FILE_MAX_AGE,
    DOWNLOAD_THREAD_WORKERS,
    MAX_FILE_SIZE_BYTES,
    COOKIES_FILE,
)
from cache import download_cache

logger = logging.getLogger(__name__)

# ─── Пул потоков для блокирующих вызовов yt-dlp ───────────────────────────────
_thread_pool = ThreadPoolExecutor(max_workers=DOWNLOAD_THREAD_WORKERS, thread_name_prefix="ydl_")

ProgressCallback = Callable[[float, str, str], Union[None, Awaitable[None]]]

# Аудиоформаты, которые Telegram принимает как audio "as is" (без перекодирования)
_TG_NATIVE_AUDIO_EXTS = {"mp3", "m4a", "ogg", "flac", "wav"}

# ─── Паттерны для очистки заголовков ──────────────────────────────────────────
_JUNK_RE = re.compile(
    r"\b(top\s*hits?|trending|official\s*(music\s*)?(video|audio|lyric[s]?)?|"
    r"lyrics?|\bhd\b|\b4k\b|mv|vevo|remaster(ed)?|explicit|clean|radio\s*edit|"
    r"\d{4}\s*(hits?|music|chart[s]?))\b",
    re.IGNORECASE,
)
_BRACKETS_RE = re.compile(r"\[.*?\]|\(.*?\)")


def _clean_title(raw: str) -> str:
    """Убирает мусорные слова и лишние скобки из названия."""
    s = _BRACKETS_RE.sub("", raw)
    s = _JUNK_RE.sub("", s)
    return re.sub(r"\s{2,}", " ", s).strip(" -|•·")


def _parse_artist_title(info: dict) -> tuple[str, str]:
    """Извлекает (artist, title) из метаданных yt-dlp."""
    artist = (info.get("artist") or "").strip()
    track = (info.get("track") or "").strip()

    if artist and track:
        return artist, _clean_title(track)

    uploader = (info.get("uploader") or info.get("channel") or "").strip()
    raw_title = (info.get("title") or "").strip()

    if " - " in raw_title:
        parts = raw_title.split(" - ", 1)
        return parts[0].strip(), _clean_title(parts[1])

    return uploader, _clean_title(raw_title)


def _display(artist: str, title: str) -> str:
    return f"{artist} - {title}" if artist else title


def _with_cookies(base_opts: dict) -> dict:
    """Добавляет cookies к опциям, если файл существует."""
    opts = dict(base_opts)
    if COOKIES_FILE.exists():
        opts["cookiefile"] = str(COOKIES_FILE)
    else:
        logger.warning("No cookies.txt found! Downloads may fail for age-restricted videos.")
    return opts


def _find_file_by_id(video_id: str, suffix: str = "") -> Optional[Path]:
    """
    Ищет скачанный файл СТРОГО по video_id (и опциональному суффиксу типа '_720p').
    Это надёжнее поиска "самого свежего файла", т.к. не ломается при
    параллельных скачиваниях нескольких пользователей одновременно.
    """
    if not video_id:
        return None

    prefix = f"{video_id}{suffix}"
    matches = sorted(
        (f for f in DOWNLOADS_DIR.glob(f"{prefix}.*") if f.is_file()),
        key=lambda p: p.stat().st_size,
        reverse=True,
    )
    if matches:
        logger.info(f"✅ [{video_id}] Найден файл: {matches[0].name}")
        return matches[0]

    logger.warning(f"⚠️ [{video_id}] Файл с суффиксом '{suffix}' не найден в {DOWNLOADS_DIR}")
    return None


def _convert_to_mp3(input_path: Path, video_id: str) -> Optional[Path]:
    """Конвертирует аудиофайл в MP3 (используется только как fallback)."""
    try:
        mp3_path = input_path.with_suffix(".mp3")
        logger.info(f"🔄 [{video_id}] Конвертирую {input_path.name} → mp3...")

        result = subprocess.run(
            [
                "ffmpeg", "-y", "-i", str(input_path),
                "-vn", "-c:a", "libmp3lame", "-b:a", "192k",
                str(mp3_path),
            ],
            capture_output=True, text=True, timeout=120,
        )

        if mp3_path.exists() and mp3_path.stat().st_size > 0:
            logger.info(f"✅ [{video_id}] Сконвертировано: {mp3_path.name}")
            if input_path != mp3_path:
                input_path.unlink(missing_ok=True)
            return mp3_path

        logger.error(f"❌ [{video_id}] Конвертация не удалась: {result.stderr[:300]}")
        return None
    except Exception as e:
        logger.error(f"[{video_id}] Ошибка конвертации: {e}")
        return None


def _make_progress_hook(video_id: str, loop: asyncio.AbstractEventLoop,
                         progress_callback: ProgressCallback):
    """
    Строит progress_hook для yt-dlp.
    yt-dlp вызывает hook в РАБОЧЕМ ПОТОКЕ (не в event loop), поэтому
    коллбек нужно планировать через run_coroutine_threadsafe /
    call_soon_threadsafe, а не через asyncio.create_task (упадёт с ошибкой,
    т.к. в потоке нет своего running loop).
    """
    last_reported = -1.0

    def hook(d: dict):
        nonlocal last_reported
        try:
            if d.get("status") != "downloading":
                return

            total = d.get("total_bytes") or d.get("total_bytes_estimate") or 0
            downloaded = d.get("downloaded_bytes") or 0
            percent = (downloaded / total * 100) if total else 0.0
            percent = max(0.0, min(100.0, percent))

            # Не спамим апдейтами — раз в ~7%
            if percent - last_reported < 7 and percent < 99:
                return
            last_reported = percent

            speed = d.get("_speed_str", "—").strip() or "—"
            eta = d.get("_eta_str", "—").strip() or "—"

            if asyncio.iscoroutinefunction(progress_callback):
                asyncio.run_coroutine_threadsafe(
                    progress_callback(percent, speed, eta), loop
                )
            else:
                loop.call_soon_threadsafe(progress_callback, percent, speed, eta)
        except Exception as e:
            logger.debug(f"[{video_id}] progress_hook error: {e}")

    return hook


def _sync_download_audio(
    url: str,
    loop: asyncio.AbstractEventLoop,
    progress_callback: Optional[ProgressCallback] = None,
) -> Optional[tuple[Path, dict]]:
    """
    Скачивает аудио (синхронно, выполняется в потоке ThreadPoolExecutor).
    Пробует форматы из AUDIO_FORMAT_FALLBACKS по очереди, пока один не сработает.
    Возвращает (путь_к_файлу, метаданные) или None.
    """
    t0 = time.monotonic()
    video_id_hint = ""
    last_error: Optional[Exception] = None

    for attempt, fmt in enumerate(AUDIO_FORMAT_FALLBACKS, start=1):
        opts = _with_cookies(dict(YTDLP_AUDIO_OPTS))
        opts["format"] = fmt

        if progress_callback:
            opts["progress_hooks"] = [
                _make_progress_hook(video_id_hint or url, loop, progress_callback)
            ]

        try:
            with yt_dlp.YoutubeDL(opts) as ydl:
                info = ydl.extract_info(url, download=True)

            if not info:
                raise RuntimeError("yt-dlp вернул пустой info")

            video_id = info.get("id", "")
            video_id_hint = video_id
            file_path = _find_file_by_id(video_id)

            if not file_path:
                raise FileNotFoundError(f"Файл для id={video_id} не найден после скачивания")

            # Если формат не воспринимается Telegram как аудио нативно — конвертируем.
            if file_path.suffix.lstrip(".").lower() not in _TG_NATIVE_AUDIO_EXTS:
                converted = _convert_to_mp3(file_path, video_id)
                if converted:
                    file_path = converted

            elapsed = time.monotonic() - t0
            artist, title = _parse_artist_title(info)
            logger.info(
                f"✅ [{video_id}] Аудио скачано за {elapsed:.1f}с "
                f"(попытка {attempt}/{len(AUDIO_FORMAT_FALLBACKS)}) | {artist} - {title} | {file_path.name}"
            )
            return file_path, info

        except Exception as e:
            last_error = e
            logger.warning(f"[{video_id_hint or url}] Формат '{fmt}' не сработал: {e}")
            continue

    logger.error(f"❌ Все форматы скачивания не сработали для {url}: {last_error}")
    return None


def _sync_search(query: str, max_results: int) -> list[dict]:
    """Быстрый поиск через ytsearch (extract_flat — без загрузки полных метаданных)."""
    opts = {
        "quiet": True,
        "no_warnings": True,
        "extract_flat": True,
        "skip_download": True,
        "socket_timeout": 8,
        "cookiefile": str(COOKIES_FILE) if COOKIES_FILE.exists() else None,
        "extractor_args": {
            "youtube": {
                "player_client": ["android"],
                "skip": ["dash", "hls", "webpage"],
            }
        },
    }

    try:
        with yt_dlp.YoutubeDL(opts) as ydl:
            info = ydl.extract_info(f"ytsearch{max_results}:{query}", download=False)
            entries = (info or {}).get("entries", [])
            results = []
            for e in entries[:max_results]:
                if not e:
                    continue
                raw_title = e.get("title", "")
                uploader = (e.get("uploader") or e.get("channel") or "").strip()
                if " - " in raw_title:
                    parts = raw_title.split(" - ", 1)
                    artist, title = parts[0].strip(), _clean_title(parts[1])
                else:
                    artist, title = uploader, _clean_title(raw_title)
                vid_id = e.get("id", "")
                results.append({
                    "display_title": _display(artist, title),
                    "title": title,
                    "artist": artist,
                    "url": f"https://www.youtube.com/watch?v={vid_id}",
                    "duration": e.get("duration", 0),
                    "views": e.get("view_count", 0),
                    "uploader": uploader,
                    "id": vid_id,
                })
            return results
    except Exception as e:
        logger.error(f"Search error '{query}': {e}")
        return []


def _sync_get_trending() -> list[dict]:
    """Трендовые треки из публичного плейлиста (с фолбэком на поиск)."""
    playlist_url = "https://www.youtube.com/playlist?list=PLFgquLnL59alCl_2TQvOiD5Vgm1hCaGSI"
    opts = {
        "quiet": True,
        "no_warnings": True,
        "extract_flat": True,
        "skip_download": True,
        "playlistend": 15,
        "socket_timeout": 8,
        "cookiefile": str(COOKIES_FILE) if COOKIES_FILE.exists() else None,
    }
    try:
        with yt_dlp.YoutubeDL(opts) as ydl:
            info = ydl.extract_info(playlist_url, download=False)
            entries = (info or {}).get("entries", [])
            results = []
            for e in (entries or [])[:10]:
                if not e:
                    continue
                raw_title = e.get("title", "")
                uploader = (e.get("uploader") or e.get("channel") or "").strip()
                if " - " in raw_title:
                    parts = raw_title.split(" - ", 1)
                    artist, title = parts[0].strip(), _clean_title(parts[1])
                else:
                    artist, title = uploader, _clean_title(raw_title)
                vid_id = e.get("id", "")
                results.append({
                    "display_title": _display(artist, title),
                    "title": title,
                    "artist": artist,
                    "url": f"https://www.youtube.com/watch?v={vid_id}",
                    "duration": e.get("duration", 0),
                    "id": vid_id,
                })
            if results:
                return results
    except Exception as e:
        logger.warning(f"Trending playlist failed: {e}")
    return _sync_search("top hits", 10)


def _sync_extract_info(url: str) -> dict:
    """Метаданные видео без скачивания (для превью по ссылке)."""
    opts = {
        "quiet": True,
        "no_warnings": True,
        "skip_download": True,
        "noplaylist": True,
        "socket_timeout": 8,
        "extractor_args": {"youtube": {"player_client": ["android"]}},
        "cookiefile": str(COOKIES_FILE) if COOKIES_FILE.exists() else None,
    }
    try:
        with yt_dlp.YoutubeDL(opts) as ydl:
            return ydl.extract_info(url, download=False) or {}
    except Exception as e:
        logger.error(f"extract_info error: {e}")
        return {}


def _sync_download_video(url: str, quality: str) -> Optional[tuple[Path, dict]]:
    """Скачивает видео синхронно (выполняется в потоке)."""
    from url_utils import detect_platform
    
    platform = detect_platform(url)
    
    # 🔥 ДЛЯ INSTAGRAM И TIKTOK - СПЕЦИАЛЬНЫЕ НАСТРОЙКИ
    if platform in ["instagram", "tiktok"]:
        opts = {
            "quiet": True,
            "no_warnings": True,
            "noplaylist": True,
            "extract_flat": False,
            "skip_download": False,
            "ignoreerrors": True,
            "no_color": True,
            "headers": {
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
            },
            "format": "best",  # ← Просто "best" без указания расширения
            "outtmpl": str(DOWNLOADS_DIR / "%(id)s_best.%(ext)s"),
        }
        if COOKIES_FILE.exists():
            opts["cookiefile"] = str(COOKIES_FILE)
        suffix = "_best"
    else:
        # Для YouTube - стандартные настройки
        opts = _with_cookies(dict(YTDLP_VIDEO_OPTS.get(quality, YTDLP_VIDEO_OPTS["best"])))
        suffix = "_best" if quality == "best" else f"_{quality}"

    try:
        with yt_dlp.YoutubeDL(opts) as ydl:
            info = ydl.extract_info(url, download=True)
        if not info:
            return None

        video_id = info.get("id", "")
        file_path = _find_file_by_id(video_id, suffix)
        if not file_path:
            # Пробуем найти без суффикса
            file_path = _find_file_by_id(video_id, "")
        if not file_path:
            logger.error(f"❌ [{video_id}] Видеофайл не найден после скачивания")
            return None

        return file_path, info
    except Exception as e:
        logger.error(f"Video download error: {e}")
        return None


# ─── Async публичный API ───────────────────────────────────────────────────────

async def search_music(query: str, max_results: Optional[int] = None) -> list[dict]:
    """Ищет треки по запросу. Результат кешируется на уровне handlers.py."""
    from config import SEARCH_MAX_RESULTS
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(
        _thread_pool, _sync_search, query, max_results or SEARCH_MAX_RESULTS
    )


async def get_trending() -> list[dict]:
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(_thread_pool, _sync_get_trending)


async def download_audio(
    url: str,
    progress_callback: Optional[ProgressCallback] = None,
) -> Optional[tuple[Path, str, str]]:
    """
    Скачивает аудио в фоновом потоке (бот не блокируется).
    Возвращает (путь_к_файлу, artist, title) или None.
    Кешируется на CACHE_TTL_DOWNLOAD секунд — повторный запрос на ту же
    ссылку отдаётся мгновенно без повторного скачивания.
    """
    cache_key = f"audio:{url}"
    cached = await download_cache.get(cache_key)
    if cached:
        path = Path(cached["path"])
        if path.exists():
            logger.info(f"📦 Cache hit (audio): {url}")
            return path, cached["artist"], cached["title"]
        await download_cache.delete(cache_key)

    loop = asyncio.get_running_loop()
    result = await loop.run_in_executor(
        _thread_pool, _sync_download_audio, url, loop, progress_callback
    )
    if not result:
        return None

    file_path, info = result

    if file_path.stat().st_size > MAX_FILE_SIZE_BYTES:
        logger.warning(f"[{info.get('id')}] Файл слишком большой: {file_path.stat().st_size} байт")
        file_path.unlink(missing_ok=True)
        return None

    artist, title = _parse_artist_title(info)
    await download_cache.set(
        cache_key,
        {"path": str(file_path), "artist": artist, "title": title},
        CACHE_TTL_DOWNLOAD,
    )
    return file_path, artist, title


async def download_video(url: str, quality: str = "best") -> Optional[tuple[Path, str, str]]:
    """Скачивает видео с YouTube (best/720p/480p). Возвращает (путь, artist, title)."""
    loop = asyncio.get_running_loop()
    result = await loop.run_in_executor(_thread_pool, _sync_download_video, url, quality)
    if not result:
        return None
    file_path, info = result
    artist, title = _parse_artist_title(info)
    return file_path, artist, title


async def get_video_info(url: str) -> dict:
    """Получает метаданные видео без скачивания (для превью по ссылке)."""
    loop = asyncio.get_running_loop()
    info = await loop.run_in_executor(_thread_pool, _sync_extract_info, url)
    if not info:
        return {}
    artist, title = _parse_artist_title(info)
    return {
        "title": title,
        "artist": artist,
        "display_title": _display(artist, title),
        "uploader": info.get("uploader", ""),
        "duration": info.get("duration", 0),
        "thumbnail": info.get("thumbnail", ""),
        "view_count": info.get("view_count", 0),
    }


def cleanup_file(path: Optional[Path]):
    """Удаляет файл с диска, если он существует (вызывать ПОСЛЕ отправки в Telegram)."""
    if path and path.exists():
        try:
            path.unlink()
            logger.debug(f"🗑️ Удалено: {path}")
        except Exception as e:
            logger.warning(f"Не удалось удалить {path}: {e}")


async def periodic_downloads_cleanup(interval: int = 900, max_age: int = DOWNLOAD_FILE_MAX_AGE):
    """
    Фоновая задача: раз в `interval` секунд удаляет из DOWNLOADS_DIR файлы
    старше `max_age` секунд. НЕ трогает свежие файлы — безопасно при
    параллельных скачиваниях. Запускать через:
        asyncio.create_task(periodic_downloads_cleanup())
    в main() (muzqq.py).
    """
    while True:
        await asyncio.sleep(interval)
        now = time.time()
        removed = 0
        for f in DOWNLOADS_DIR.iterdir():
            try:
                if f.is_file() and (now - f.stat().st_mtime) > max_age:
                    f.unlink()
                    removed += 1
            except Exception as e:
                logger.debug(f"cleanup: не удалось удалить {f}: {e}")
        if removed:
            logger.info(f"🧹 Фоновая очистка: удалено {removed} старых файлов из {DOWNLOADS_DIR}")


def format_duration(seconds: int) -> str:
    if not seconds:
        return "??:??"
    h, rem = divmod(int(seconds), 3600)
    m, s = divmod(rem, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"


def format_views(count: int) -> str:
    if not count:
        return "0"
    if count >= 1_000_000:
        return f"{count/1_000_000:.1f}M"
    if count >= 1_000:
        return f"{count/1_000:.0f}K"
    return str(count)
