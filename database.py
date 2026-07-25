"""
📊 База данных — статистика, пользователи, лимиты
aiosqlite (async SQLite). Для 1000+ запросов/день на один SQLite-файл
включаем WAL (позволяет читать во время записи) и busy_timeout (вместо
мгновенной ошибки "database is locked" соединение подождёт снятия блокировки).
"""

import aiosqlite
import logging
from contextlib import asynccontextmanager
from datetime import date
from typing import AsyncIterator, Optional

from config import DB_PATH, SQLITE_BUSY_TIMEOUT_MS

logger = logging.getLogger(__name__)


@asynccontextmanager
async def _connect() -> AsyncIterator[aiosqlite.Connection]:
    """Открывает соединение с уже применёнными PRAGMA (WAL + busy_timeout)."""
    async with aiosqlite.connect(DB_PATH, timeout=SQLITE_BUSY_TIMEOUT_MS / 1000) as db:
        await db.execute("PRAGMA journal_mode=WAL")
        await db.execute(f"PRAGMA busy_timeout={SQLITE_BUSY_TIMEOUT_MS}")
        yield db


async def init_db() -> None:
    """Создаёт таблицы и индексы при первом запуске."""
    async with _connect() as db:
        await db.executescript("""
            CREATE TABLE IF NOT EXISTS users (
                user_id     INTEGER PRIMARY KEY,
                username    TEXT,
                first_name  TEXT,
                is_premium  INTEGER DEFAULT 0,
                is_banned   INTEGER DEFAULT 0,
                created_at  TEXT DEFAULT (datetime('now'))
            );

            CREATE TABLE IF NOT EXISTS downloads (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id     INTEGER,
                url         TEXT,
                media_type  TEXT,
                quality     TEXT,
                file_size   INTEGER,
                created_at  TEXT DEFAULT (datetime('now'))
            );

            CREATE TABLE IF NOT EXISTS daily_limits (
                user_id     INTEGER,
                dl_date     TEXT,
                count       INTEGER DEFAULT 0,
                PRIMARY KEY (user_id, dl_date)
            );

            CREATE INDEX IF NOT EXISTS idx_downloads_user ON downloads(user_id);
            CREATE INDEX IF NOT EXISTS idx_downloads_created ON downloads(created_at);
        """)
        await db.commit()
    logger.info("✅ База данных инициализирована (WAL включён)")


async def register_user(user_id: int, username: str, first_name: str) -> None:
    """Регистрирует или обновляет пользователя.

    Вызывается из middleware на каждое сообщение — в middlewares.py это
    обёрнуто в user_cache, чтобы не писать в SQLite на КАЖДОЕ сообщение,
    а только когда данные пользователя реально могли устареть.
    """
    async with _connect() as db:
        await db.execute("""
            INSERT INTO users (user_id, username, first_name)
            VALUES (?, ?, ?)
            ON CONFLICT(user_id) DO UPDATE SET
                username   = excluded.username,
                first_name = excluded.first_name
        """, (user_id, username, first_name))
        await db.commit()


async def get_user(user_id: int) -> Optional[dict]:
    """Возвращает данные пользователя или None, если не найден."""
    async with _connect() as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("SELECT * FROM users WHERE user_id = ?", (user_id,)) as cur:
            row = await cur.fetchone()
            return dict(row) if row else None


async def is_user_banned(user_id: int) -> bool:
    user = await get_user(user_id)
    return bool(user and user["is_banned"])


async def get_daily_downloads(user_id: int) -> int:
    """Возвращает кол-во скачиваний пользователя за сегодня."""
    today = str(date.today())
    async with _connect() as db:
        async with db.execute(
            "SELECT count FROM daily_limits WHERE user_id=? AND dl_date=?",
            (user_id, today),
        ) as cur:
            row = await cur.fetchone()
            return row[0] if row else 0


async def increment_daily_downloads(user_id: int) -> None:
    """Увеличивает счётчик скачиваний на 1 (атомарно, без гонок)."""
    today = str(date.today())
    async with _connect() as db:
        await db.execute("""
            INSERT INTO daily_limits (user_id, dl_date, count)
            VALUES (?, ?, 1)
            ON CONFLICT(user_id, dl_date) DO UPDATE SET
                count = count + 1
        """, (user_id, today))
        await db.commit()


async def log_download(user_id: int, url: str, media_type: str, quality: str, file_size: int) -> None:
    async with _connect() as db:
        await db.execute("""
            INSERT INTO downloads (user_id, url, media_type, quality, file_size)
            VALUES (?, ?, ?, ?, ?)
        """, (user_id, url, media_type, quality, file_size))
        await db.commit()


async def get_stats() -> dict:
    """Статистика для админа. Один connect, четыре быстрых запроса по индексам."""
    async with _connect() as db:
        async with db.execute("SELECT COUNT(*) FROM users") as cur:
            total_users = (await cur.fetchone())[0]
        async with db.execute(
            "SELECT COUNT(DISTINCT user_id) FROM downloads WHERE created_at >= datetime('now', '-1 day')"
        ) as cur:
            active_today = (await cur.fetchone())[0]
        async with db.execute("SELECT COUNT(*) FROM downloads") as cur:
            total_downloads = (await cur.fetchone())[0]
        async with db.execute("SELECT COUNT(*) FROM users WHERE is_premium=1") as cur:
            premium_users = (await cur.fetchone())[0]

    return {
        "total_users": total_users,
        "active_today": active_today,
        "total_downloads": total_downloads,
        "premium_users": premium_users,
    }


async def set_premium(user_id: int, value: bool) -> None:
    async with _connect() as db:
        await db.execute("UPDATE users SET is_premium=? WHERE user_id=?", (int(value), user_id))
        await db.commit()


async def set_banned(user_id: int, value: bool) -> None:
    async with _connect() as db:
        await db.execute("UPDATE users SET is_banned=? WHERE user_id=?", (int(value), user_id))
        await db.commit()


async def list_users(limit: int = 20) -> list[dict]:
    async with _connect() as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT * FROM users ORDER BY created_at DESC LIMIT ?", (limit,)
        ) as cur:
            rows = await cur.fetchall()
            return [dict(r) for r in rows]
