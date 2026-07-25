"""
🎤 Shazam — заглушка

shazamio требует нативных зависимостей, которые не всегда доступны на
Railway. Пока распознавание отключено — функции возвращают None, а
handlers.py показывает пользователю понятное сообщение об ошибке вместо
падения. Если нужно включить: поставь shazamio в requirements.txt и
замени тела функций на реальные вызовы shazamio.Shazam().
"""

import logging
from typing import Optional

logger = logging.getLogger(__name__)


async def recognize_from_bytes(audio_bytes: bytes, suffix: str = ".ogg") -> Optional[dict]:
    logger.warning("⚠️ Shazam отключен на этом деплое")
    return None


async def search_song_by_text(query: str) -> Optional[dict]:
    return None
