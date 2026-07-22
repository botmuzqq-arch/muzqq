"""
🎤 Shazam — распознавание музыки по аудио/голосовому
Использует shazamio (async)
"""

import asyncio
import logging
import os
import tempfile
from pathlib import Path
from typing import Optional

from shazamio import Shazam

logger = logging.getLogger(__name__)

# Singleton клиент
_shazam: Optional[Shazam] = None


def get_shazam() -> Shazam:
    global _shazam
    if _shazam is None:
        _shazam = Shazam()
        logger.info("✅ Shazam client initialized")
    return _shazam


async def recognize_song(audio_path: Path) -> Optional[dict]:
    """
    Распознаёт песню по аудиофайлу.
    Возвращает dict с данными или None.
    """
    if not audio_path.exists():
        logger.error(f"Audio file not found: {audio_path}")
        return None

    try:
        shazam = get_shazam()
        logger.info(f"🎤 Recognizing: {audio_path}")
        
        # 🔥 ПРОБУЕМ РАЗНЫЕ МЕТОДЫ
        try:
            # Метод 1: прямой
            out = await shazam.recognize(str(audio_path))
        except Exception as e:
            logger.warning(f"Method 1 failed: {e}")
            # Метод 2: через bytes
            with open(audio_path, 'rb') as f:
                audio_bytes = f.read()
            out = await shazam.recognize_song(audio_bytes)
        
        logger.info(f"Shazam response: {out.keys() if out else 'None'}")

        # Разбираем ответ
        track = out.get("track") if out else None
        if not track:
            logger.warning("No track found in response")
            return None

        # Извлекаем данные
        title = track.get("title", "")
        subtitle = track.get("subtitle", "")
        
        # Пытаемся найти ссылку на YouTube
        youtube_url = None
        hub = track.get("hub", {})
        providers = hub.get("providers", [])
        for provider in providers:
            if provider.get("type") == "YOUTUBE":
                actions = provider.get("actions", [])
                for action in actions:
                    if "uri" in action:
                        youtube_url = action["uri"]
                        break
        
        # Обложка
        images = track.get("images", {})
        cover = images.get("coverarthq") or images.get("coverart") or ""

        result = {
            "title": title,
            "artist": subtitle,
            "album": track.get("sections", [{}])[0].get("metadata", [{}])[0].get("text", ""),
            "genre": track.get("genres", {}).get("primary", ""),
            "cover": cover,
            "youtube_url": youtube_url,
            "shazam_url": track.get("url", ""),
        }
        
        logger.info(f"✅ Recognized: {result['artist']} - {result['title']}")
        return result

    except Exception as e:
        logger.error(f"Shazam recognition error: {e}")
        import traceback
        logger.error(traceback.format_exc())
        return None


async def recognize_from_bytes(audio_bytes: bytes, suffix: str = ".ogg") -> Optional[dict]:
    """
    Распознаёт песню прямо из байтов.
    """
    from config import TEMP_DIR
    
    try:
        # Создаём временный файл
        with tempfile.NamedTemporaryFile(dir=TEMP_DIR, suffix=suffix, delete=False) as tmp:
            tmp.write(audio_bytes)
            tmp_path = Path(tmp.name)
        
        logger.info(f"📁 Temp file created: {tmp_path} ({len(audio_bytes)} bytes)")
        
        # Проверяем, что файл не пустой
        if len(audio_bytes) < 1000:
            logger.warning("Audio file too small!")
            tmp_path.unlink(missing_ok=True)
            return None
        
        # Распознаём
        result = await recognize_song(tmp_path)
        
        # Удаляем временный файл
        tmp_path.unlink(missing_ok=True)
        
        return result
        
    except Exception as e:
        logger.error(f"Recognize from bytes error: {e}")
        return None


# 🔥 НОВАЯ ФУНКЦИЯ - поиск через Shazam по тексту
async def search_song_by_text(query: str) -> Optional[dict]:
    """
    Ищет песню по тексту через Shazam.
    """
    try:
        shazam = get_shazam()
        
        # Ищем в топе
        results = await shazam.search_track(query=query, limit=1)
        
        if results and 'tracks' in results and results['tracks']['hits']:
            track = results['tracks']['hits'][0]['track']
            return {
                "title": track.get("title", ""),
                "artist": track.get("subtitle", ""),
                "cover": track.get("images", {}).get("coverarthq", ""),
                "youtube_url": None,  # shazamio не даёт ссылку при поиске
                "shazam_url": track.get("url", ""),
            }
        
        return None
        
    except Exception as e:
        logger.error(f"Search by text error: {e}")
        return None