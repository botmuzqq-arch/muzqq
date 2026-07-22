"""
🛡️ Middleware — антиспам + регистрация пользователей
"""

import time
import logging
from collections import defaultdict
from typing import Callable, Any

from aiogram import BaseMiddleware
from aiogram.types import Message, TelegramObject

from config import RATE_LIMIT_REQUESTS, RATE_LIMIT_WINDOW
from database import register_user, is_user_banned

logger = logging.getLogger(__name__)


class AntiSpamMiddleware(BaseMiddleware):
    """
    Ограничивает количество запросов пользователя.
    Также регистрирует новых пользователей и проверяет бан.
    """

    def __init__(self):
        # user_id -> list of timestamps
        self._requests: dict[int, list[float]] = defaultdict(list)
        super().__init__()

    async def __call__(
        self,
        handler: Callable,
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        if not isinstance(event, Message):
            return await handler(event, data)

        user = event.from_user
        if not user:
            return await handler(event, data)

        user_id = user.id

        # Регистрируем / обновляем пользователя
        await register_user(
            user_id=user_id,
            username=user.username or "",
            first_name=user.first_name or "",
        )

        # Проверяем бан
        if await is_user_banned(user_id):
            await event.answer("🚫 Вы заблокированы в этом боте.")
            return

        # Проверяем rate limit
        now = time.monotonic()
        timestamps = self._requests[user_id]

        # Убираем старые записи за пределами окна
        self._requests[user_id] = [t for t in timestamps if now - t < RATE_LIMIT_WINDOW]

        if len(self._requests[user_id]) >= RATE_LIMIT_REQUESTS:
            await event.answer(
                f"⏳ Слишком много запросов. Подождите немного.\n"
                f"Лимит: {RATE_LIMIT_REQUESTS} запросов / {RATE_LIMIT_WINDOW} сек."
            )
            logger.warning(f"Rate limit exceeded for user {user_id}")
            return

        self._requests[user_id].append(now)

        return await handler(event, data)