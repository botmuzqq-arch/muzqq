"""
🔗 Утилиты для определения платформы по URL
"""

import re
from config import PLATFORM_PATTERNS

# Regex для детекции URL в тексте
URL_REGEX = re.compile(
    r"https?://(?:[-\w.]|(?:%[\da-fA-F]{2})|[/?#&=@:,;!$'()*+~])+",
    re.IGNORECASE,
)


def extract_url(text: str) -> str | None:
    """Извлекает первый URL из текста."""
    match = URL_REGEX.search(text)
    return match.group(0) if match else None


def detect_platform(url: str) -> str | None:
    """
    Определяет платформу по URL.
    Возвращает: "youtube" | "tiktok" | "instagram" | None
    """
    url_lower = url.lower()
    for platform, patterns in PLATFORM_PATTERNS.items():
        if any(p in url_lower for p in patterns):
            return platform
    return None


def is_supported_url(url: str) -> bool:
    """Проверяет, поддерживается ли URL."""
    return detect_platform(url) is not None


PLATFORM_EMOJIS = {
    "youtube": "▶️ YouTube",
    "tiktok": "🎵 TikTok",
    "instagram": "📸 Instagram",
}


def platform_label(platform: str) -> str:
    return PLATFORM_EMOJIS.get(platform, "🌐 Сайт")

def extract_video_id(url: str, platform: str) -> str:
    """Извлекает короткий ID видео из URL."""
    import re
    
    if platform == "instagram":
        # Instagram: https://www.instagram.com/reel/XXXXX/
        match = re.search(r'/(?:reel|p|tv)/([^/?]+)', url)
        if match:
            return match.group(1)
    
    elif platform == "tiktok":
        # TikTok: https://www.tiktok.com/@user/video/XXXXX
        match = re.search(r'/video/(\d+)', url)
        if match:
            return match.group(1)
    
    elif platform == "youtube":
        # YouTube: watch?v=XXXXX или youtu.be/XXXXX
        match = re.search(r'(?:v=|youtu\.be/)([^&/?]+)', url)
        if match:
            return match.group(1)
    
    # Если не нашли - возвращаем хеш от URL (короткий)
    import hashlib
    return hashlib.md5(url.encode()).hexdigest()[:10]