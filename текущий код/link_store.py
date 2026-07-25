"""
🔗 link_store — короткий реестр URL для callback_data

ПРОБЛЕМА: Telegram даёт BUTTON_DATA_INVALID, если callback_data > 64 байт.
Ссылки Instagram/TikTok (особенно с трекинг-параметрами ?igsh=...) легко
превышают этот лимит, если пихать их в callback_data напрямую.

РЕШЕНИЕ: регистрируем URL здесь, получаем короткий 12-символьный ID,
кладём в callback_data ТОЛЬКО этот ID. При нажатии кнопки — резолвим
ID обратно в URL. Тот же URL всегда даёт тот же ID (md5), поэтому
повторные регистрации не плодят дубликаты.
"""

import hashlib
import time
import logging

logger = logging.getLogger(__name__)

# Сколько хранить сопоставление id -> url (сек). Кнопки живут не вечно —
# 2 часов с запасом хватает даже на "залежавшиеся" сообщения в чате.
_TTL = 7200

# short_id -> (url, expires_at)
_store: dict[str, tuple[str, float]] = {}


def register(url: str) -> str:
    """Регистрирует URL и возвращает короткий ID (12 символов, ~влезает в callback_data)."""
    short_id = hashlib.md5(url.encode("utf-8")).hexdigest()[:12]
    _store[short_id] = (url, time.monotonic() + _TTL)
    return short_id


def resolve(short_id: str) -> str | None:
    """Возвращает исходный URL по короткому ID, либо None если не найден/истёк."""
    entry = _store.get(short_id)
    if entry is None:
        logger.warning(f"link_store: неизвестный short_id={short_id!r}")
        return None

    url, expires_at = entry
    if time.monotonic() > expires_at:
        _store.pop(short_id, None)
        logger.warning(f"link_store: short_id={short_id!r} истёк")
        return None

    return url


def cleanup_expired():
    """Чистит устаревшие записи (вызывать из фоновой задачи)."""
    now = time.monotonic()
    expired = [k for k, (_, exp) in _store.items() if now > exp]
    for k in expired:
        _store.pop(k, None)
    if expired:
        logger.debug(f"link_store: очищено {len(expired)} устаревших ссылок")
