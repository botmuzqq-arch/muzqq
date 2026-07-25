"""
🔗 Утилиты для определения платформы по URL
"""

import re
import hashlib
from typing import Optional

from config import PLATFORM_PATTERNS, PLATFORM_EMOJIS

URL_REGEX = re.compile(
    r"https?://(?:[-\w.]|(?:%[\da-fA-F]{2})|[/?#&=@:,;!$'()*+~])+",
    re.IGNORECASE,
)


def extract_url(text: str) -> Optional[str]:
    """Извлекает первый URL из текста."""
    match = URL_REGEX.search(text)
    return match.group(0) if match else None


def detect_platform(url: str) -> Optional[str]:
    """Определяет платформу по URL: 'youtube' | 'tiktok' | 'instagram' | ... | None."""
    url_lower = url.lower()
    for platform, patterns in PLATFORM_PATTERNS.items():
        if any(p in url_lower for p in patterns):
            return platform
    return None


def is_supported_url(url: str) -> bool:
    """Проверяет, поддерживается ли URL."""
    return detect_platform(url) is not None


def platform_label(platform: Optional[str]) -> str:
    return PLATFORM_EMOJIS.get(platform, "🌐 Сайт")


def extract_video_id(url: str, platform: str) -> str:
    """Извлекает короткий ID видео из URL (используется как fallback-идентификатор)."""
    if platform == "instagram":
        match = re.search(r"/(?:reel|p|tv)/([^/?]+)", url)
        if match:
            return match.group(1)
    elif platform == "tiktok":
        match = re.search(r"/video/(\d+)", url)
        if match:
            return match.group(1)
    elif platform == "youtube":
        match = re.search(r"(?:v=|youtu\.be/)([^&/?]+)", url)
        if match:
            return match.group(1)

    return hashlib.md5(url.encode()).hexdigest()[:10]
