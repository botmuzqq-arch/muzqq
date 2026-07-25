"""
⌨️ Клавиатуры — кнопки бота

🔴 ВАЖНО: callback_data в Telegram ограничен 64 байтами. Ссылки на
Instagram/TikTok с трекинг-параметрами (?igsh=..., ?_r=...) легко превышают
этот лимит и Telegram отклоняет кнопку целиком (BUTTON_DATA_INVALID) —
пользователь просто не увидит кнопку. Поэтому здесь мы НИКОГДА не кладём
сырой URL в callback_data напрямую — только короткий id из link_store.
"""

from aiogram.types import (
    InlineKeyboardMarkup, InlineKeyboardButton,
    ReplyKeyboardMarkup, KeyboardButton,
)
from aiogram.utils.keyboard import InlineKeyboardBuilder

from link_store import register as register_link
from config import BOT_USERNAME


def main_menu_keyboard() -> ReplyKeyboardMarkup:
    """Главное меню (reply-кнопки внизу экрана)."""
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="🔍 Поиск музыки"), KeyboardButton(text="🔥 Топ треки")],
            [KeyboardButton(text="🎤 Распознать песню"), KeyboardButton(text="💎 Premium")],
            [KeyboardButton(text="❓ Помощь"), KeyboardButton(text="📊 Статистика")],
        ],
        resize_keyboard=True,
        input_field_placeholder="Введите название песни или ссылку...",
    )


def search_results_keyboard(results: list[dict]) -> InlineKeyboardMarkup:
    """Кнопки с результатами поиска — сразу ведут на скачивание MP3."""
    builder = InlineKeyboardBuilder()

    for item in results[:5]:
        label = (item.get("display_title") or item.get("title") or "Без названия")[:40]
        url = item.get("url", "")
        if not url:
            continue

        short_id = register_link(url)
        builder.row(
            InlineKeyboardButton(text=f"🎵 {label}", callback_data=f"dl_audio|{short_id}")
        )

    builder.row(InlineKeyboardButton(text="⭐ Премиум — безлимит", callback_data="premium_info"))
    builder.row(InlineKeyboardButton(text="❌ Закрыть", callback_data="cancel"))
    return builder.as_markup()


def link_action_keyboard(url: str, platform: str) -> InlineKeyboardMarkup:
    """Кнопки выбора формата после того, как пользователь прислал ссылку YouTube."""
    short_id = register_link(url)
    builder = InlineKeyboardBuilder()

    builder.row(
        InlineKeyboardButton(text="🎵 Скачать MP3", callback_data=f"dl_audio|{short_id}")
    )
    builder.row(
        InlineKeyboardButton(text="📹 1080p", callback_data=f"dl_video|1080p|{short_id}"),
        InlineKeyboardButton(text="📹 720p", callback_data=f"dl_video|720p|{short_id}"),
        InlineKeyboardButton(text="📹 480p", callback_data=f"dl_video|480p|{short_id}"),
    )
    builder.row(InlineKeyboardButton(text="⭐ Премиум", callback_data="premium_info"))
    builder.row(InlineKeyboardButton(text="❌ Отмена", callback_data="cancel"))
    return builder.as_markup()


def shazam_result_keyboard(url: str) -> InlineKeyboardMarkup:
    """Кнопки после распознавания Shazam."""
    short_id = register_link(url)
    builder = InlineKeyboardBuilder()
    builder.row(InlineKeyboardButton(text="🎵 Скачать MP3", callback_data=f"dl_audio|{short_id}"))
    builder.row(InlineKeyboardButton(text="⭐ Премиум", callback_data="premium_info"))
    builder.row(InlineKeyboardButton(text="❌ Отмена", callback_data="cancel"))
    return builder.as_markup()


def admin_keyboard() -> InlineKeyboardMarkup:
    """Панель администратора."""
    builder = InlineKeyboardBuilder()
    builder.row(
        InlineKeyboardButton(text="📊 Статистика", callback_data="admin_stats"),
        InlineKeyboardButton(text="👥 Пользователи", callback_data="admin_users"),
    )
    builder.row(InlineKeyboardButton(text="❌ Закрыть", callback_data="cancel"))
    return builder.as_markup()


def download_format_keyboard(url: str) -> InlineKeyboardMarkup:
    """Клавиатура выбора формата (MP3 / видео best) для результата поиска."""
    short_id = register_link(url)
    builder = InlineKeyboardBuilder()
    builder.row(
        InlineKeyboardButton(text="🎵 MP3", callback_data=f"dl_audio|{short_id}"),
        InlineKeyboardButton(text="📹 Видео Best", callback_data=f"dl_video|best|{short_id}"),
    )
    builder.row(InlineKeyboardButton(text="❌ Отмена", callback_data="cancel"))
    return builder.as_markup()


def premium_keyboard() -> InlineKeyboardMarkup:
    """Клавиатура для раздела Premium с оплатой Stars."""
    builder = InlineKeyboardBuilder()
    builder.row(
        InlineKeyboardButton(
            text="💎 Купить Premium (400 Stars)",
            callback_data="buy_premium"
        )
    )
    builder.row(
        InlineKeyboardButton(
            text="🔙 Назад",
            callback_data="back_to_menu"
        )
    )
    return builder.as_markup()
