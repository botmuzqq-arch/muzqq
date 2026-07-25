"""
⚡ Кеш — in-memory dict с TTL + опционально Redis

Redis нужен, если бот когда-нибудь будет запущен НЕСКОЛЬКИМИ инстансами
(например, на Railway с несколькими репликами) — тогда in-memory кеш
каждого процесса свой, и без общего Redis кеш перестаёт быть эффективным.
Для одного инстанса in-memory вариант работает отлично и ничего
дополнительно разворачивать не нужно.
"""

import asyncio
import time
import json
import logging
from typing import Any, Optional

from config import USE_REDIS, REDIS_URL

logger = logging.getLogger(__name__)

_redis_available = USE_REDIS
_redis_client = None

if _redis_available:
    try:
        import redis.asyncio as redis  # noqa: F401  (проверяем, что пакет вообще есть)
    except ImportError:
        logger.warning("⚠️ Пакет redis не установлен — падаю обратно на in-memory кеш")
        _redis_available = False


async def _get_redis():
    """Ленивая инициализация общего Redis-клиента (один на процесс)."""
    global _redis_client
    if _redis_client is None:
        import redis.asyncio as redis
        _redis_client = redis.from_url(REDIS_URL, decode_responses=True)
        await _redis_client.ping()
        logger.info("✅ Соединение с Redis установлено")
    return _redis_client


class TTLCache:
    """Кеш с поддержкой Redis (если настроен) или in-memory (всегда как fallback)."""

    def __init__(self, name: str = "default"):
        self._name = name
        self._store: dict[str, tuple[Any, float]] = {}
        self._lock = asyncio.Lock()
        self._use_redis = _redis_available

    async def get(self, key: str) -> Optional[Any]:
        if self._use_redis:
            try:
                client = await _get_redis()
                raw = await client.get(f"{self._name}:{key}")
                if raw is not None:
                    return json.loads(raw)
                return None
            except Exception as e:
                logger.warning(f"Redis get error ({self._name}:{key}): {e}, использую in-memory")

        async with self._lock:
            entry = self._store.get(key)
            if entry is None:
                return None
            value, expires_at = entry
            if time.monotonic() > expires_at:
                del self._store[key]
                return None
            return value

    async def set(self, key: str, value: Any, ttl: int = 300) -> None:
        if self._use_redis:
            try:
                client = await _get_redis()
                await client.setex(f"{self._name}:{key}", ttl, json.dumps(value))
                return
            except Exception as e:
                logger.warning(f"Redis set error ({self._name}:{key}): {e}, использую in-memory")

        async with self._lock:
            self._store[key] = (value, time.monotonic() + ttl)

    async def delete(self, key: str) -> None:
        if self._use_redis:
            try:
                client = await _get_redis()
                await client.delete(f"{self._name}:{key}")
            except Exception as e:
                logger.debug(f"Redis delete error ({self._name}:{key}): {e}")

        async with self._lock:
            self._store.pop(key, None)

    async def clear_expired(self) -> None:
        """Чистит устаревшие in-memory записи (Redis сам удаляет по TTL)."""
        now = time.monotonic()
        async with self._lock:
            expired = [k for k, (_, exp) in self._store.items() if now > exp]
            for k in expired:
                del self._store[k]
        if expired:
            logger.debug(f"Cache {self._name}: очищено {len(expired)} устаревших записей")

    def __len__(self) -> int:
        return len(self._store)


# ─── Глобальные экземпляры кеша ────────────────────────────────────────────────
search_cache = TTLCache("search")
download_cache = TTLCache("download")
user_cache = TTLCache("user")


async def periodic_cache_cleanup(interval: int = 600) -> None:
    """Фоновая задача — чистит in-memory кеш и реестр коротких ссылок каждые N секунд."""
    from link_store import cleanup_expired as cleanup_link_store

    while True:
        await asyncio.sleep(interval)
        try:
            await search_cache.clear_expired()
            await download_cache.clear_expired()
            await user_cache.clear_expired()
            cleanup_link_store()
        except Exception as e:
            logger.error(f"Ошибка фоновой очистки кеша: {e}")
