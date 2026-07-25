"""
🎮 Хендлеры — всё в одном роутере
Структура:
  /start, /help        — приветствие
  /search <query>      — поиск
  /top, /trending      — топ треки
  /admin               — панель администратора
  /stats, /users       — команды администратора
  Текст                — автодетект URL или поисковый запрос
  Аудио/Голосовое      — Shazam-распознавание
  Inline callbacks     — скачивание, выбор качества
"""

import asyncio
import logging
import time
from pathlib import Path
from typing import Optional

from aiogram import Router, F, Bot
from aiogram.enums import ChatAction
from aiogram.filters import Command, CommandStart
from aiogram.types import (
    Message, CallbackQuery,
    FSInputFile,
)
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup

from config import (
    ADMIN_IDS, FREE_DOWNLOADS_PER_DAY, PREMIUM_DOWNLOADS_PER_DAY,
    CACHE_TTL_SEARCH, SEARCH_MAX_RESULTS,
)
from keyboards import (
    main_menu_keyboard, link_action_keyboard,
    search_results_keyboard, download_format_keyboard,
    shazam_result_keyboard, admin_keyboard,
)
from downloader import (
    search_music, get_trending, download_audio, download_video,
    get_video_info, cleanup_file, format_duration, format_views,
)
from shazam_service import recognize_from_bytes
from url_utils import extract_url, detect_platform, platform_label
from link_store import resolve as resolve_url
from cache import search_cache
from database import (
    get_stats, list_users, set_premium, set_banned,
    get_daily_downloads, increment_daily_downloads, log_download, get_user,
)

logger = logging.getLogger(__name__)
router = Router()

# ─── FSM States ───────────────────────────────────────────────────────────────


class SearchState(StatesGroup):
    waiting_for_query = State()


# Временное хранилище результатов поиска (session-level, user_id -> list[dict])
_search_sessions: dict[int, list[dict]] = {}

# Как часто (в % прогресса) обновлять статусное сообщение — экономим Telegram API
_PROGRESS_STEP = 12


# ─── Вспомогательные функции ──────────────────────────────────────────────────

def is_admin(user_id: int) -> bool:
    return user_id in ADMIN_IDS


async def check_download_limit(user_id: int) -> tuple[bool, int, int]:
    """Проверяет лимит скачиваний. Возвращает (allowed, used, limit)."""
    user = await get_user(user_id)
    is_premium = bool(user and user.get("is_premium", 0))
    limit = PREMIUM_DOWNLOADS_PER_DAY if is_premium else FREE_DOWNLOADS_PER_DAY
    used = await get_daily_downloads(user_id)
    return used < limit, used, limit


def _progress_bar(percent: float, width: int = 12) -> str:
    filled = int(width * percent / 100)
    return "▓" * filled + "░" * (width - filled)


def make_progress_updater(status_msg: Message, video_id_hint: str = ""):
    """
    Создаёт async-коллбек прогресса, который троттлит обновления
    статусного сообщения (не чаще, чем раз в _PROGRESS_STEP процентов).
    """
    state = {"last": -100.0}

    async def update(percent: float, speed: str, eta: str):
        if percent - state["last"] < _PROGRESS_STEP and percent < 99:
            return
        state["last"] = percent
        bar = _progress_bar(percent)
        try:
            await status_msg.edit_text(
                f"🎵 Скачиваю... {percent:.0f}%\n"
                f"[{bar}]\n"
                f"⚡ {speed} · ⏳ ETA {eta}"
            )
        except Exception as e:
            logger.debug(f"[{video_id_hint}] progress edit failed (ignoring): {e}")

    return update


async def send_audio_file(
    message: Message,
    file_path,
    title: str,
    performer: str = "",
    status_msg: Optional[Message] = None,
) -> int:
    """Отправляет аудиофайл в чат. Возвращает размер файла (0 при ошибке)."""
    try:
        if isinstance(file_path, str):
            file_path = Path(file_path)

        if not file_path or not file_path.exists():
            logger.error(f"File not found: {file_path}")
            if status_msg:
                await status_msg.edit_text("❌ Файл не найден.")
            return 0

        file_size = file_path.stat().st_size
        clean_title = str(title).replace("/", "_").replace("\\", "_").replace(":", "_")[:64]
        clean_performer = (
            str(performer).replace("/", "_").replace("\\", "_").replace(":", "_")[:64]
            if performer else ""
        )
        ext = file_path.suffix or ".mp3"
        input_file = FSInputFile(str(file_path), filename=f"{clean_title}{ext}")

        await message.answer_audio(
            audio=input_file,
            title=clean_title[:64],
            performer=clean_performer[:64] if clean_performer else None,
            caption=f"🎵 <b>{clean_title}</b>\n💿 {clean_performer}" if clean_performer else f"🎵 <b>{clean_title}</b>",
        )
        if status_msg:
            await status_msg.delete()
        return file_size
    except Exception as e:
        logger.error(f"Error sending audio: {e}")
        if status_msg:
            await status_msg.edit_text(f"❌ Ошибка при отправке: {str(e)[:100]}")
        return 0


async def send_video_file(
    message: Message,
    file_path: Path,
    title: str,
    status_msg: Optional[Message] = None,
) -> int:
    """Отправляет видеофайл в чат."""
    try:
        file_size = file_path.stat().st_size
        input_file = FSInputFile(file_path, filename=f"{title}.mp4")

        await message.answer_video(video=input_file, caption=f"📹 <b>{title}</b>")
        if status_msg:
            await status_msg.delete()
        return file_size
    except Exception as e:
        logger.error(f"Error sending video: {e}")
        if status_msg:
            await status_msg.edit_text("❌ Ошибка при отправке файла.")
        return 0


# ─── /start ───────────────────────────────────────────────────────────────────

@router.message(CommandStart())
async def cmd_start(message: Message):
    name = message.from_user.first_name or "друг"
    await message.answer(
        f"👋 Привет, <b>{name}</b>!\n\n"
        "🎵 Я — <b>Music Bot</b>, твой персональный музыкальный сервис.\n\n"
        "<b>Что умею:</b>\n"
        "🔍 Искать музыку по названию или артисту\n"
        "📥 Скачивать аудио и видео (YouTube, TikTok, Instagram)\n"
        "🎤 Распознавать песни (как Shazam)\n"
        "🔥 Показывать топ трендовых треков\n\n"
        "Просто напиши название песни или отправь ссылку! 👇",
        reply_markup=main_menu_keyboard(),
    )


# ─── /help ────────────────────────────────────────────────────────────────────

@router.message(Command("help"))
@router.message(F.text == "ℹ️ Помощь")
async def cmd_help(message: Message):
    await message.answer(
        "📖 <b>Инструкция по использованию:</b>\n\n"
        "🔍 <b>Поиск музыки:</b>\n"
        "  Напишите название песни или «Исполнитель — Трек»\n"
        "  Пример: <code>Imagine Dragons Believer</code>\n\n"
        "🔗 <b>Скачивание по ссылке:</b>\n"
        "  Отправьте ссылку YouTube / TikTok / Instagram\n"
        "  Выберите: аудио (MP3) или видео (разные качества)\n\n"
        "🎤 <b>Распознавание песни:</b>\n"
        "  Отправьте голосовое сообщение или аудиофайл\n"
        "  Бот определит трек, как Shazam!\n\n"
        "🔥 <b>Топ треки:</b>\n"
        "  Нажмите кнопку «Топ треки» в меню\n\n"
        "⚡ <b>Команды:</b>\n"
        "  /start — главное меню\n"
        "  /search — поиск\n"
        "  /top — трендовые треки\n"
        "  /help — эта справка\n",
    )


# ─── /search ──────────────────────────────────────────────────────────────────

@router.message(Command("search"))
@router.message(F.text == "🔍 Поиск музыки")
async def cmd_search(message: Message, state: FSMContext):
    if message.text and message.text.startswith("/search "):
        query = message.text[8:].strip()
        if query:
            await perform_search(message, query)
            return

    await message.answer(
        "🔍 Введите название песни, артиста или поисковый запрос:\n\n"
        "<i>Примеры:</i>\n"
        "  • <code>Bohemian Rhapsody</code>\n"
        "  • <code>Imagine Dragons</code>\n"
        "  • <code>AC DC Highway to Hell</code>",
    )
    await state.set_state(SearchState.waiting_for_query)


@router.message(SearchState.waiting_for_query)
async def process_search_query(message: Message, state: FSMContext):
    await state.clear()
    query = message.text.strip() if message.text else ""
    if not query:
        await message.answer("⚠️ Пустой запрос. Попробуйте ещё раз.")
        return
    await perform_search(message, query)


async def perform_search(message: Message, query: str):
    """Выполняет поиск (с кешем на CACHE_TTL_SEARCH секунд) и показывает результаты."""
    user_id = message.from_user.id
    await message.bot.send_chat_action(message.chat.id, ChatAction.TYPING)

    cache_key = f"search:{query.lower().strip()}"
    cached = await search_cache.get(cache_key)

    if cached:
        results = cached
        logger.info(f"📦 Search cache hit: '{query}'")
    else:
        status = await message.answer(f"🔍 Ищу: <b>{query}</b>...")
        t0 = time.monotonic()
        results = await search_music(query, SEARCH_MAX_RESULTS)
        logger.info(f"⏱️ Поиск '{query}' занял {time.monotonic() - t0:.2f}с")
        await status.delete()

        if not results:
            await message.answer("😔 Ничего не найдено. Попробуйте другой запрос.")
            return

        await search_cache.set(cache_key, results, CACHE_TTL_SEARCH)

    _search_sessions[user_id] = results

    lines = [f"🎵 <b>Результаты поиска:</b> «{query}»\n"]
    for i, item in enumerate(results, 1):
        dur = format_duration(item.get("duration", 0))
        lines.append(f"{i}. {item['title']} [{dur}]")

    await message.answer("\n".join(lines), reply_markup=search_results_keyboard(results))


# ─── Callback: выбор трека из поиска ─────────────────────────────────────────

@router.callback_query(F.data.startswith("search_pick|"))
async def cb_search_pick(call: CallbackQuery):
    idx = int(call.data.split("|")[1])
    user_id = call.from_user.id

    results = _search_sessions.get(user_id, [])
    if idx >= len(results):
        await call.answer("⚠️ Сессия устарела. Повторите поиск.", show_alert=True)
        return

    item = results[idx]
    url = item["url"]
    title = item["title"]
    uploader = item.get("uploader", "")
    dur = format_duration(item.get("duration", 0))
    views = format_views(item.get("views", 0))

    text = (
        f"🎵 <b>{title}</b>\n"
        f"👤 {uploader}\n"
        f"⏱ {dur}  👁 {views}\n\n"
        "Выберите формат для скачивания:"
    )
    await call.message.edit_text(text, reply_markup=download_format_keyboard(url))
    await call.answer()


# ─── /top / trending ─────────────────────────────────────────────────────────

@router.message(Command("top"))
@router.message(F.text == "🔥 Топ треки")
async def cmd_trending(message: Message):
    await message.bot.send_chat_action(message.chat.id, ChatAction.TYPING)
    status = await message.answer("🔥 Загружаю трендовые треки...")

    cache_key = "trending:global"
    cached = await search_cache.get(cache_key)

    if cached:
        results = cached
    else:
        results = await get_trending()
        if results:
            await search_cache.set(cache_key, results, 1800)  # 30 мин

    await status.delete()

    if not results:
        await message.answer("😔 Не удалось загрузить треки. Попробуйте позже.")
        return

    user_id = message.from_user.id
    _search_sessions[user_id] = results

    lines = ["🔥 <b>Топ трендовых треков:</b>\n"]
    for i, item in enumerate(results[:10], 1):
        lines.append(f"{i}. {item['title']}")

    await message.answer("\n".join(lines), reply_markup=search_results_keyboard(results))


# ─── Обработка текстовых сообщений (URL или поиск) ───────────────────────────

@router.message(F.text)
async def handle_text(message: Message, state: FSMContext):
    text = message.text.strip()

    url = extract_url(text)
    if url:
        platform = detect_platform(url)
        if platform:
            await handle_url(message, url, platform)
            return
        await message.answer(
            "⚠️ Эта платформа не поддерживается.\n"
            "Поддерживаются: YouTube, TikTok, Instagram."
        )
        return

    if len(text) < 2:
        await message.answer("⚠️ Запрос слишком короткий.")
        return

    await perform_search(message, text)


async def handle_url(message: Message, url: str, platform: str):
    """Показывает кнопки действий для ссылки."""
    label = platform_label(platform)
    await message.bot.send_chat_action(message.chat.id, ChatAction.TYPING)
    status = await message.answer(f"🔗 Получаю информацию о {label}...")

    info = await get_video_info(url)
    await status.delete()

    title = info.get("title", "Видео")
    uploader = info.get("uploader", "")
    dur = format_duration(info.get("duration", 0))
    views = format_views(info.get("view_count", 0))

    # 🔥 ДЛЯ INSTAGRAM И TIKTOK - СРАЗУ СКАЧИВАЕМ!
    if platform in ["instagram", "tiktok"]:
        status_msg = await message.answer(
            f"📥 Скачиваю видео из {label}...\n"
            f"📌 <b>{title}</b>\n"
            f"⏳ Пожалуйста, подождите..."
        )
        
        user_id = message.from_user.id
        allowed, used, limit = await check_download_limit(user_id)
        if not allowed:
            await status_msg.edit_text(f"❌ Лимит: {used}/{limit} в день")
            return

        start_time = time.time()
        result = await download_video(url, "best")
        elapsed = time.time() - start_time

        if not result:
            await status_msg.edit_text(
                "❌ Не удалось скачать видео.\n"
                "Попробуйте позже или отправьте другую ссылку."
            )
            return

        file_path, artist, title = result
        display_title = f"{artist} - {title}" if artist else title

        await status_msg.edit_text(f"📤 Отправляю видео... ({elapsed:.1f}с)")
        file_size = await send_video_file(message, file_path, display_title, status_msg)

        if file_size:
            await increment_daily_downloads(user_id)
            await log_download(user_id, url, "video", "best", file_size)
            logger.info(f"✅ Видео отправлено за {elapsed:.1f}с — пользователь {user_id}")
            cleanup_file(file_path)
        
    else:
        # ДЛЯ YOUTUBE - ПОКАЗЫВАЕМ КНОПКИ С ВЫБОРОМ КАЧЕСТВА
        caption = (
            f"{label}\n\n"
            f"📌 <b>{title}</b>\n"
            f"👤 {uploader}\n"
            f"⏱ {dur}  👁 {views}\n\n"
            "Что скачать?"
        )
        await message.answer(caption, reply_markup=link_action_keyboard(url, platform))


# ─── Callback: скачать аудио ─────────────────────────────────────────────────

@router.callback_query(F.data.startswith("dl_audio|"))
async def cb_download_audio(call: CallbackQuery):
    short_id = call.data[len("dl_audio|"):]
    url = resolve_url(short_id)
    if not url:
        # 🔴 ИСПРАВЛЕНО: раньше здесь лежал сырой длинный URL и был риск
        # BUTTON_DATA_INVALID. Теперь это короткий ID — если он не
        # резолвится (истёк TTL / бот перезапускался), просим повторить.
        await call.answer("⚠️ Ссылка устарела. Отправьте её ещё раз.", show_alert=True)
        return
    user_id = call.from_user.id

    allowed, used, limit = await check_download_limit(user_id)
    if not allowed:
        await call.answer(f"❌ Лимит: {used}/{limit} в день", show_alert=True)
        return

    await call.answer("⏳ Загрузка...")
    await call.bot.send_chat_action(call.message.chat.id, ChatAction.UPLOAD_VOICE)

    status = await call.message.answer(f"🎵 Скачиваю... 0%\n[{_progress_bar(0)}]")
    progress_updater = make_progress_updater(status)

    start_time = time.time()
    result = await download_audio(url, progress_callback=progress_updater)
    elapsed = time.time() - start_time

    if not result:
        await status.edit_text(
            "❌ Не удалось скачать аудио.\n\n"
            "Возможные причины:\n"
            "• Видео недоступно в вашем регионе\n"
            "• Требуется авторизация (добавьте cookies.txt)\n"
            "• Неподдерживаемый формат\n\n"
            "Попробуйте другое видео."
        )
        return

    file_path, artist, title = result
    logger.info(f"⏱️ Скачивание аудио заняло {elapsed:.1f}с | {artist} - {title}")

    await status.edit_text(f"📤 Отправляю... ({elapsed:.1f}с)")
    file_size = await send_audio_file(call.message, file_path, title or "audio", artist, status)

    if file_size:
        await increment_daily_downloads(user_id)
        await log_download(user_id, url, "audio", "mp3", file_size)
        logger.info(f"✅ Готово за {elapsed:.1f}с — пользователь {user_id}")


# ─── Callback: скачать видео ──────────────────────────────────────────────────

@router.callback_query(F.data.startswith("dl_video|"))
async def cb_download_video(call: CallbackQuery):
    _, quality, short_id = call.data.split("|", 2)
    url = resolve_url(short_id)
    if not url:
        await call.answer("⚠️ Ссылка устарела. Отправьте её ещё раз.", show_alert=True)
        return
    user_id = call.from_user.id

    allowed, used, limit = await check_download_limit(user_id)
    if not allowed:
        await call.answer(f"❌ Лимит: {used}/{limit} в день", show_alert=True)
        return

    await call.answer("⏳ Загрузка видео...")
    await call.bot.send_chat_action(call.message.chat.id, ChatAction.UPLOAD_VIDEO)

    status = await call.message.answer(f"📹 Скачиваю видео ({quality})...\nЭто может занять до минуты.")

    start_time = time.time()
    result = await download_video(url, quality)
    elapsed = time.time() - start_time

    if not result:
        await status.edit_text("❌ Не удалось скачать видео. Попробуйте другое качество.")
        return

    file_path, artist, title = result
    display_title = f"{artist} - {title}" if artist else title

    await status.edit_text(f"📤 Отправляю... ({elapsed:.1f}с)")
    file_size = await send_video_file(call.message, file_path, display_title, status)

    if file_size:
        await increment_daily_downloads(user_id)
        await log_download(user_id, url, "video", quality, file_size)
        logger.info(f"✅ Видео отправлено за {elapsed:.1f}с — пользователь {user_id}")
        cleanup_file(file_path)  # видео не кешируем — сразу удаляем с диска


# ─── Callback: отмена ────────────────────────────────────────────────────────

@router.callback_query(F.data == "cancel")
async def cb_cancel(call: CallbackQuery):
    await call.message.delete()
    await call.answer("Отменено.")


# ─── Голосовые и аудио сообщения → Shazam ────────────────────────────────────

@router.message(F.voice)
async def handle_voice(message: Message, bot: Bot):
    await _recognize_audio(message, bot, message.voice.file_id, ".ogg")


@router.message(F.audio)
async def handle_audio(message: Message, bot: Bot):
    suffix = ".mp3"
    if message.audio.mime_type:
        mt = message.audio.mime_type
        if "ogg" in mt:
            suffix = ".ogg"
        elif "wav" in mt:
            suffix = ".wav"
        elif "flac" in mt:
            suffix = ".flac"
    await _recognize_audio(message, bot, message.audio.file_id, suffix)


async def _recognize_audio(message: Message, bot: Bot, file_id: str, suffix: str):
    """Распознаёт аудио через Shazam."""
    await bot.send_chat_action(message.chat.id, ChatAction.TYPING)
    status = await message.answer("🎤 Распознаю музыку... Подождите 5-10 секунд...")

    try:
        file = await bot.get_file(file_id)
        await status.edit_text("📥 Скачиваю аудиофайл...")

        audio_bytes = await bot.download_file(file.file_path)
        audio_data = audio_bytes.read() if hasattr(audio_bytes, "read") else audio_bytes

        file_size = len(audio_data)
        logger.info(f"📁 Downloaded {file_size} bytes")

        if file_size < 5000:
            await status.edit_text("❌ Аудиофайл слишком короткий! (менее 5KB)")
            return

        await status.edit_text(f"🎤 Распознаю... (файл: {file_size // 1024}KB)")
        result = await recognize_from_bytes(audio_data, suffix)

        if not result:
            await status.edit_text(
                "😔 Не удалось распознать песню.\n\n"
                "Возможные причины:\n"
                "• Музыка слишком тихая или с шумом\n"
                "• Фрагмент слишком короткий\n"
                "• Неподдерживаемый формат\n\n"
                "💡 Попробуй записать более чистый фрагмент!"
            )
            return

        title = result.get("title", "Неизвестно")
        artist = result.get("artist", "Неизвестно")
        album = result.get("album", "")
        genre = result.get("genre", "")
        youtube_url = result.get("youtube_url") or result.get("shazam_url", "")

        text = f"🎉 <b>Песня найдена!</b>\n\n🎵 <b>{title}</b>\n👤 {artist}\n"
        if album:
            text += f"💿 {album}\n"
        if genre:
            text += f"🎼 {genre}\n"

        markup = None
        if youtube_url:
            markup = shazam_result_keyboard(youtube_url)
            text += "\n📥 Нажми кнопку, чтобы скачать MP3!"

        await status.edit_text(text, reply_markup=markup)
        logger.info(f"✅ Recognized: {artist} - {title}")

    except Exception as e:
        logger.error(f"Voice recognition error: {e}")
        await status.edit_text("❌ Произошла ошибка при распознавании.\nПопробуй позже.")


# ─── Команда /recognize (кнопка в меню) ──────────────────────────────────────

@router.message(F.text == "🎤 Распознать песню")
async def cmd_recognize(message: Message):
    await message.answer(
        "🎤 <b>Распознавание музыки</b>\n\n"
        "Отправьте <b>голосовое сообщение</b> или <b>аудиофайл</b> с музыкой — "
        "я определю песню, как Shazam!\n\n"
        "<i>Лучше всего работает с чистой музыкой без шума.</i>"
    )


# ─── Админ-панель ────────────────────────────────────────────────────────────

@router.message(Command("admin"))
async def cmd_admin(message: Message):
    if not is_admin(message.from_user.id):
        await message.answer("⛔ У вас нет прав администратора.")
        return
    await message.answer("🔧 <b>Панель администратора</b>\n\nВыберите действие:", reply_markup=admin_keyboard())


@router.message(Command("stats"))
async def cmd_stats(message: Message):
    if not is_admin(message.from_user.id):
        await message.answer("⛔ Нет прав.")
        return

    stats = await get_stats()
    await message.answer(
        "📊 <b>Статистика бота:</b>\n\n"
        f"👥 Всего пользователей: <b>{stats['total_users']}</b>\n"
        f"⚡ Активных сегодня: <b>{stats['active_today']}</b>\n"
        f"📥 Всего скачиваний: <b>{stats['total_downloads']}</b>\n"
        f"⭐ Premium пользователей: <b>{stats['premium_users']}</b>"
    )


@router.message(Command("users"))
async def cmd_users(message: Message):
    if not is_admin(message.from_user.id):
        await message.answer("⛔ Нет прав.")
        return

    users = await list_users(20)
    if not users:
        await message.answer("Пользователей пока нет.")
        return

    lines = ["👥 <b>Последние пользователи:</b>\n"]
    for u in users:
        premium = "⭐" if u["is_premium"] else ""
        banned = "🚫" if u["is_banned"] else ""
        name = u.get("first_name") or u.get("username") or str(u["user_id"])
        lines.append(f"• {name} {premium}{banned} — <code>{u['user_id']}</code>")

    await message.answer("\n".join(lines))


@router.message(Command("premium"))
async def cmd_premium(message: Message):
    """Выдать/снять premium: /premium <user_id> <on|off>"""
    if not is_admin(message.from_user.id):
        return

    parts = message.text.split()
    if len(parts) < 3:
        await message.answer("Использование: /premium <user_id> <on|off>")
        return

    try:
        uid = int(parts[1])
        val = parts[2].lower() == "on"
        await set_premium(uid, val)
        status = "выдан ⭐" if val else "снят"
        await message.answer(f"Premium {status} для пользователя {uid}.")
    except Exception as e:
        await message.answer(f"Ошибка: {e}")


@router.message(Command("ban"))
async def cmd_ban(message: Message):
    """Забанить/разбанить: /ban <user_id> <on|off>"""
    if not is_admin(message.from_user.id):
        return

    parts = message.text.split()
    if len(parts) < 3:
        await message.answer("Использование: /ban <user_id> <on|off>")
        return

    try:
        uid = int(parts[1])
        val = parts[2].lower() == "on"
        await set_banned(uid, val)
        status = "заблокирован 🚫" if val else "разблокирован ✅"
        await message.answer(f"Пользователь {uid} {status}.")
    except Exception as e:
        await message.answer(f"Ошибка: {e}")


# ─── Callback: admin actions ─────────────────────────────────────────────────

@router.callback_query(F.data == "admin_stats")
async def cb_admin_stats(call: CallbackQuery):
    if not is_admin(call.from_user.id):
        await call.answer("⛔ Нет прав.", show_alert=True)
        return

    stats = await get_stats()
    await call.message.edit_text(
        "📊 <b>Статистика бота:</b>\n\n"
        f"👥 Всего пользователей: <b>{stats['total_users']}</b>\n"
        f"⚡ Активных сегодня: <b>{stats['active_today']}</b>\n"
        f"📥 Всего скачиваний: <b>{stats['total_downloads']}</b>\n"
        f"⭐ Premium пользователей: <b>{stats['premium_users']}</b>",
        reply_markup=admin_keyboard(),
    )
    await call.answer()


@router.callback_query(F.data == "admin_users")
async def cb_admin_users(call: CallbackQuery):
    if not is_admin(call.from_user.id):
        await call.answer("⛔ Нет прав.", show_alert=True)
        return

    users = await list_users(10)
    lines = ["👥 <b>Последние 10 пользователей:</b>\n"]
    for u in users:
        p = "⭐" if u["is_premium"] else ""
        b = "🚫" if u["is_banned"] else ""
        name = u.get("first_name") or u.get("username") or str(u["user_id"])
        lines.append(f"• {name} {p}{b} — <code>{u['user_id']}</code>")

    await call.message.edit_text("\n".join(lines), reply_markup=admin_keyboard())
    await call.answer()
