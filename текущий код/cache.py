"""
⚡ Кеш — in-memory dict с TTL + опционально Redis
"""

import asyncio
import time
import logging
import json
from typing import Any, Optional

from config import USE_REDIS, REDIS_URL

logger = logging.getLogger(__name__)

# Пытаемся импортировать Redis
if USE_REDIS:
    try:
        import redis.asyncio as redis
        _redis_client = None

        async def get_redis():
            global _redis_client
            if _redis_client is None:
                _redis_client = redis.from_url(REDIS_URL, decode_responses=True)
                await _redis_client.ping()
                logger.info("✅ Redis connection established")
            return _redis_client
    except ImportError:
        logger.warning("⚠️ Redis import failed, falling back to in-memory cache")
        USE_REDIS = False


class TTLCache:
    """Кеш с поддержкой Redis или in-memory."""

    def __init__(self, name: str = "default"):
        self._name = name
        self._store: dict[str, tuple[Any, float]] = {}
        self._lock = asyncio.Lock()
        self._use_redis = USE_REDIS

    async def get(self, key: str) -> Any | None:
        if self._use_redis:
            try:
                redis_client = await get_redis()
                value = await redis_client.get(f"{self._name}:{key}")
                if value:
                    return json.loads(value)
            except Exception as e:
                logger.warning(f"Redis get error: {e}, falling back to memory")

        # In-memory fallback
        async with self._lock:
            entry = self._store.get(key)
            if entry is None:
                return None
            value, expires_at = entry
            if time.monotonic() > expires_at:
                del self._store[key]
                return None
            return value

    async def set(self, key: str, value: Any, ttl: int = 300):
        if self._use_redis:
            try:
                redis_client = await get_redis()
                await redis_client.setex(
                    f"{self._name}:{key}",
                    ttl,
                    json.dumps(value)
                )
                return
            except Exception as e:
                logger.warning(f"Redis set error: {e}, falling back to memory")

        # In-memory fallback
        async with self._lock:
            self._store[key] = (value, time.monotonic() + ttl)

    async def delete(self, key: str):
        if self._use_redis:
            try:
                redis_client = await get_redis()
                await redis_client.delete(f"{self._name}:{key}")
                return
            except Exception:
                pass

        async with self._lock:
            self._store.pop(key, None)

    async def clear_expired(self):
        """Очистка устаревших записей."""
        now = time.monotonic()
        async with self._lock:
            expired = [k for k, (_, exp) in self._store.items() if now > exp]
            for k in expired:
                del self._store[k]
            if expired:
                logger.debug(f"Cache {self._name}: cleared {len(expired)} expired entries.")

    def __len__(self):
        return len(self._store)


# Глобальные экземпляры кеша
search_cache = TTLCache("search")
download_cache = TTLCache("download")
user_cache = TTLCache("user")


async def periodic_cache_cleanup(interval: int = 600):
    """Фоновая задача — чистит кеш каждые N секунд."""
    from link_store import cleanup_expired as cleanup_link_store

    while True:
        await asyncio.sleep(interval)
        await search_cache.clear_expired()
        await download_cache.clear_expired()
        await user_cache.clear_expired()
        cleanup_link_store()
