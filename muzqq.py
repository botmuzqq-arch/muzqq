"""
🎵 Music Bot — Telegram Music Service
Powered by aiogram 3, yt-dlp, shazamio
"""

import asyncio
import logging
import sys
from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode

from config import BOT_TOKEN
from handlers import router
from middlewares import AntiSpamMiddleware
from database import init_db
from cache import periodic_cache_cleanup
from downloader import periodic_downloads_cleanup

# Настройка логирования
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler("bot.log", encoding="utf-8"),
    ],
)
logger = logging.getLogger(__name__)


# В main() после создания bot добавь:

async def main():
    logger.info("🚀 Starting Music Bot...")

    await init_db()

    bot = Bot(
        token=BOT_TOKEN,
        default=DefaultBotProperties(parse_mode=ParseMode.HTML),
    )
    
    # 🔥 Устанавливаем больший таймаут для загрузки
    bot.session.timeout = 600  # 10 минут на скачивание
 
    dp = Dispatcher()
    dp.message.middleware(AntiSpamMiddleware())
    dp.include_router(router)

    # 🔥 ИСПРАВЛЕНО: фоновые задачи очистки кеша/файлов существовали в коде,
    # но нигде не запускались — папка downloads и кеш росли бы бесконечно.
    cleanup_tasks = [
        asyncio.create_task(periodic_cache_cleanup()),
        asyncio.create_task(periodic_downloads_cleanup()),
    ]

    logger.info("✅ Bot started. Polling...")
    try:
        await dp.start_polling(bot, allowed_updates=dp.resolve_used_update_types())
    finally:
        for task in cleanup_tasks:
            task.cancel()
        await bot.session.close()
        logger.info("Bot stopped.")


if __name__ == "__main__":
    asyncio.run(main())