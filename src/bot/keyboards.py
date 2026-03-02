from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton, ReplyKeyboardMarkup, KeyboardButton
from aiogram.utils.keyboard import InlineKeyboardBuilder, ReplyKeyboardBuilder

from src.personas import get_all_personas


def get_level_keyboard() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.row(
        InlineKeyboardButton(text="Beginner", callback_data="level_beginner"),
        InlineKeyboardButton(text="Elementary", callback_data="level_elementary"),
    )
    builder.row(
        InlineKeyboardButton(text="Intermediate", callback_data="level_intermediate"),
        InlineKeyboardButton(text="Advanced", callback_data="level_advanced"),
    )
    return builder.as_markup()


def get_persona_keyboard() -> InlineKeyboardMarkup:
    """Выбор собеседника"""
    builder = InlineKeyboardBuilder()
    personas = get_all_personas()
    for key, name in personas.items():
        builder.row(InlineKeyboardButton(text=name, callback_data=f"persona_{key}"))
    return builder.as_markup()


def get_main_menu_keyboard() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.row(
        InlineKeyboardButton(text="📖 How to use", callback_data="how_to_use"),
    )
    builder.row(
        InlineKeyboardButton(text="📊 My Stats", callback_data="my_stats"),
        InlineKeyboardButton(text="📚 My Vocabulary", callback_data="my_vocabulary"),
    )
    builder.row(
        InlineKeyboardButton(text="⚙️ Settings", callback_data="settings"),
    )
    return builder.as_markup()


def get_settings_keyboard(notifications_enabled: bool) -> InlineKeyboardMarkup:
    """Меню настроек"""
    notif_text = "🔔 Notifications: ON" if notifications_enabled else "🔕 Notifications: OFF"
    builder = InlineKeyboardBuilder()
    builder.row(
        InlineKeyboardButton(text=notif_text, callback_data="toggle_notifications")
    )
    builder.row(
        InlineKeyboardButton(text="📊 Change Level", callback_data="change_level"),
    )
    builder.row(
        InlineKeyboardButton(text="🗣 Change Partner", callback_data="change_persona"),
    )
    builder.row(
        InlineKeyboardButton(text="← Back", callback_data="back_to_menu"),
    )
    return builder.as_markup()


def get_back_to_menu_keyboard() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.row(InlineKeyboardButton(text="← Back to Menu", callback_data="back_to_menu"))
    return builder.as_markup()


def get_back_to_settings_keyboard() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.row(InlineKeyboardButton(text="← Back to Settings", callback_data="settings"))
    return builder.as_markup()


def get_vocabulary_actions_keyboard() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.row(
        InlineKeyboardButton(text="🗑 Clear All", callback_data="vocab_clear"),
        InlineKeyboardButton(text="📥 Export", callback_data="vocab_export"),
    )
    builder.row(
        InlineKeyboardButton(text="← Back", callback_data="back_to_menu"),
    )
    return builder.as_markup()


# ─── Translate / Original ──────────────────────────────────────────────────

def get_translate_keyboard(message_id: int) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.row(
        InlineKeyboardButton(text="🌐 Translate", callback_data=f"translate_{message_id}")
    )
    return builder.as_markup()


def get_original_keyboard(message_id: int) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.row(
        InlineKeyboardButton(text="🔤 Original", callback_data=f"original_{message_id}")
    )
    return builder.as_markup()


# ─── Flow Mode ─────────────────────────────────────────────────────────────

def get_flow_start_keyboard() -> ReplyKeyboardMarkup:
    builder = ReplyKeyboardBuilder()
    builder.row(KeyboardButton(text="▶ Flow"))
    return builder.as_markup(resize_keyboard=True, persistent=True)


def get_flow_stop_keyboard() -> ReplyKeyboardMarkup:
    builder = ReplyKeyboardBuilder()
    builder.row(
        KeyboardButton(text="↩ Switch"),
        KeyboardButton(text="⏹ Stop Flow"),
    )
    return builder.as_markup(resize_keyboard=True, persistent=True)


def get_flow_user_voice_keyboard(message_id: int) -> InlineKeyboardMarkup:
    """Кнопки под служебным сообщением после голосового пользователя в Flow Mode"""
    builder = InlineKeyboardBuilder()
    builder.row(
        InlineKeyboardButton(text="📝 Text", callback_data=f"uvoice_text_{message_id}"),
        InlineKeyboardButton(text="🌐 Translate", callback_data=f"uvoice_translate_{message_id}"),
    )
    return builder.as_markup()
    return builder.as_markup(resize_keyboard=True, persistent=True)


def get_flow_voice_keyboard(message_id: int) -> InlineKeyboardMarkup:
    """Кнопки под голосовым сообщением в Flow Mode"""
    builder = InlineKeyboardBuilder()
    builder.row(
        InlineKeyboardButton(text="📝 Text", callback_data=f"flow_text_{message_id}"),
        InlineKeyboardButton(text="🌐 Translate", callback_data=f"flow_translate_{message_id}"),
    )
    return builder.as_markup()


def get_flow_voice_text_keyboard(message_id: int) -> InlineKeyboardMarkup:
    """После нажатия Text — показываем Translate"""
    builder = InlineKeyboardBuilder()
    builder.row(
        InlineKeyboardButton(text="🌐 Translate", callback_data=f"flow_translate_{message_id}"),
    )
    return builder.as_markup()


def get_flow_voice_translate_keyboard(message_id: int) -> InlineKeyboardMarkup:
    """После нажатия Translate — показываем Original"""
    builder = InlineKeyboardBuilder()
    builder.row(
        InlineKeyboardButton(text="🔤 Original", callback_data=f"flow_original_{message_id}"),
    )
    return builder.as_markup()
    """Inline-кнопка Switch во время активного Flow диалога"""
    builder = InlineKeyboardBuilder()
    builder.row(
        InlineKeyboardButton(
            text=f"↩ Switch ({persona_name})",
            callback_data="flow_switch"
        )
    )
    return builder.as_markup()
