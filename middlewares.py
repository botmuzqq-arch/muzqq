"""
🛡️ Middleware — антиспам + регистрация пользователей + проверка бана

🔴 ВАЖНО: скачивания в этом боте запускаются в основном через inline-кнопки
(CallbackQuery), а не текстовые команды (Message). Старая версия проверяла
рейт-лимит и бан ТОЛЬКО на Message — то есть все нажатия кнопок "Скачать"
проходили мимо антиспама и бана полностью. Здесь middleware регистрируется
и на message, и на callback_query.
"""

import time
import logging
from collections import defaultdict
from typing import Callable, Any, Union

from aiogram import BaseMiddleware
from aiogram.types import Message, CallbackQuery, TelegramObject, User

from config import RATE_LIMIT_REQUESTS, RATE_LIMIT_WINDOW, USER_REGISTER_CACHE_TTL
from database import register_user, is_user_banned
from cache import user_cache

logger = logging.getLogger(__name__)

_EventWithUser = Union[Message, CallbackQuery]


class AntiSpamMiddleware(BaseMiddleware):
    """
    Ограничивает количество запросов пользователя, регистрирует новых
    пользователей и отсекает забаненных. Работает и для сообщений, и для
    нажатий инлайн-кнопок.
    """

    def __init__(self) -> None:
        # user_id -> список таймстампов запросов за последнее окно
        self._requests: dict[int, list[float]] = defaultdict(list)
        super().__init__()

    async def __call__(
        self,
        handler: Callable,
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        if not isinstance(event, (Message, CallbackQuery)):
            return await handler(event, data)

        user: User | None = event.from_user
        if not user:
            return await handler(event, data)

        user_id = user.id

        # Регистрируем / обновляем пользователя — но не на КАЖДЫЙ апдейт,
        # а раз в USER_REGISTER_CACHE_TTL секунд на пользователя, иначе
        # при 1000+ пользователей/день SQLite получает лишний write на
        # каждое единственное сообщение или нажатие кнопки.
        cache_key = f"seen:{user_id}"
        if await user_cache.get(cache_key) is None:
            await register_user(
                user_id=user_id,
                username=user.username or "",
                first_name=user.first_name or "",
            )
            await user_cache.set(cache_key, True, USER_REGISTER_CACHE_TTL)

        # Проверяем бан — этот запрос дешёвый (индекс по PK), делаем всегда.
        if await is_user_banned(user_id):
            await self._reject(event, "🚫 Вы заблокированы в этом боте.")
            return

        # Rate limit
        now = time.monotonic()
        timestamps = self._requests[user_id]
        self._requests[user_id] = [t for t in timestamps if now - t < RATE_LIMIT_WINDOW]

        if len(self._requests[user_id]) >= RATE_LIMIT_REQUESTS:
            logger.warning(f"Rate limit exceeded for user {user_id}")
            await self._reject(
                event,
                f"⏳ Слишком много запросов. Подождите немного.\n"
                f"Лимит: {RATE_LIMIT_REQUESTS} запросов / {RATE_LIMIT_WINDOW} сек.",
            )
            return

        self._requests[user_id].append(now)

        return await handler(event, data)

    @staticmethod
    async def _reject(event: _EventWithUser, text: str) -> None:
        """Единообразно отвечает пользователю независимо от типа апдейта."""
        try:
            if isinstance(event, CallbackQuery):
                await event.answer(text, show_alert=True)
            else:
                await event.answer(text)
        except Exception as e:
            logger.debug(f"Не удалось отправить отказ пользователю: {e}")
