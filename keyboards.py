"""
⌨️ Клавиатуры — inline и reply кнопки

Логика:
  • Кнопки из ПОИСКА / ТОПА → callback "dl_audio|<short_id>" → мгновенное скачивание MP3
  • Кнопки при ССЫЛКЕ → показываем выбор (аудио / видео / качество)

🔴 ИСПРАВЛЕНО (BUTTON_DATA_INVALID):
  Раньше полный URL клался прямо в callback_data. Telegram режет
  callback_data на 64 байтах — длинные ссылки Instagram/TikTok
  (особенно с ?igsh=... трекинг-параметрами) превышали лимит и бот падал
  с ошибкой при показе клавиатуры.
  Теперь URL регистрируется в link_store, и в callback_data кладётся
  только короткий 12-символьный ID. Резолвится обратно в handlers.py.
"""

from aiogram.types import (
    InlineKeyboardMarkup, InlineKeyboardButton,
    ReplyKeyboardMarkup, KeyboardButton,
)
from aiogram.utils.keyboard import InlineKeyboardBuilder

from link_store import register as register_url


def main_menu_keyboard() -> ReplyKeyboardMarkup:
    """Главное меню (reply-кнопки внизу экрана)."""
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="🔍 Поиск музыки"), KeyboardButton(text="🔥 Топ треки")],
            [KeyboardButton(text="🎤 Распознать песню"), KeyboardButton(text="ℹ️ Помощь")],
        ],
        resize_keyboard=True,
        input_field_placeholder="Введите название песни или ссылку...",
    )


def search_results_keyboard(results: list[dict]) -> InlineKeyboardMarkup:
    """
    Кнопки с результатами поиска / топ-треков.
    Нажатие → СРАЗУ скачивает MP3 (без промежуточного меню).

    results[i] должен содержать:
      display_title — "Исполнитель - Название"
      url           — YouTube URL
    """
    builder = InlineKeyboardBuilder()
    for item in results[:10]:
        label = item.get("display_title") or item.get("title", "Без названия")
        label = label[:55]  # Telegram ограничивает длину текста кнопки
        url = item.get("url", "")
        if not url:
            continue
        short_id = register_url(url)
        builder.row(
            InlineKeyboardButton(
                text=f"🎵 {label}",
                callback_data=f"dl_audio|{short_id}",
            )
        )
    builder.row(
        InlineKeyboardButton(text="❌ Закрыть", callback_data="cancel")
    )
    return builder.as_markup()


def link_action_keyboard(url: str, platform: str) -> InlineKeyboardMarkup:
    """
    Кнопки действий при получении прямой ССЫЛКИ (YouTube/TikTok/Instagram).
    Здесь показываем выбор форматов — пользователь сам решает.
    Работает одинаково для всех платформ: URL всегда прячется за short_id,
    поэтому длинные ссылки Instagram/TikTok больше не ломают callback_data.
    """
    short_id = register_url(url)

    builder = InlineKeyboardBuilder()
    builder.row(
        InlineKeyboardButton(
            text="🎵 Аудио (MP3)",
            callback_data=f"dl_audio|{short_id}",
        )
    )
    builder.row(
        InlineKeyboardButton(text="📹 1080p", callback_data=f"dl_video|1080p|{short_id}"),
        InlineKeyboardButton(text="📹 720p", callback_data=f"dl_video|720p|{short_id}"),
        InlineKeyboardButton(text="📹 480p", callback_data=f"dl_video|480p|{short_id}"),
    )
    builder.row(
        InlineKeyboardButton(text="❌ Отмена", callback_data="cancel")
    )
    return builder.as_markup()


def shazam_result_keyboard(url: str) -> InlineKeyboardMarkup:
    """Кнопки после распознавания песни Shazam."""
    short_id = register_url(url)
    builder = InlineKeyboardBuilder()
    builder.row(
        InlineKeyboardButton(text="🎵 Скачать MP3", callback_data=f"dl_audio|{short_id}"),
        InlineKeyboardButton(text="🌐 Открыть", url=url),
    )
    return builder.as_markup()


def admin_keyboard() -> InlineKeyboardMarkup:
    """Панель администратора."""
    builder = InlineKeyboardBuilder()
    builder.row(
        InlineKeyboardButton(text="📊 Статистика", callback_data="admin_stats"),
        InlineKeyboardButton(text="👥 Пользователи", callback_data="admin_users"),
    )
    builder.row(
        InlineKeyboardButton(text="❌ Закрыть", callback_data="cancel")
    )
    return builder.as_markup()


def download_format_keyboard(url: str) -> InlineKeyboardMarkup:
    """Клавиатура выбора формата для ссылок (используется в handlers.py)."""
    short_id = register_url(url)
    builder = InlineKeyboardBuilder()
    builder.row(
        InlineKeyboardButton(text="🎵 MP3", callback_data=f"dl_audio|{short_id}"),
        InlineKeyboardButton(text="📹 Видео Best", callback_data=f"dl_video|best|{short_id}"),
    )
    builder.row(
        InlineKeyboardButton(text="❌ Отмена", callback_data="cancel")
    )
    return builder.as_markup()


def social_media_keyboard(url: str, platform: str) -> InlineKeyboardMarkup:
    """
    Клавиатура для Instagram и TikTok (используется в handle_url через
    link_action_keyboard, оставлена также как отдельная точка входа).
    Принимает полный url — прячет его за short_id так же, как остальные.
    """
    short_id = register_url(url)
    builder = InlineKeyboardBuilder()
    builder.row(
        InlineKeyboardButton(text="📹 Скачать видео", callback_data=f"dl_video|best|{short_id}")
    )
    builder.row(
        InlineKeyboardButton(text="🎵 Скачать MP3", callback_data=f"dl_audio|{short_id}")
    )
    builder.row(
        InlineKeyboardButton(text="❌ Отмена", callback_data="cancel")
    )
    return builder.as_markup()
