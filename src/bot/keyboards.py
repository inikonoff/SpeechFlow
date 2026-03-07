from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton, ReplyKeyboardMarkup, KeyboardButton
from aiogram.utils.keyboard import InlineKeyboardBuilder, ReplyKeyboardBuilder
from src.config import ADMIN_IDS

from src.personas import get_all_personas


def get_level_keyboard() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.row(
        InlineKeyboardButton(text="Beginner",     callback_data="level_beginner"),
        InlineKeyboardButton(text="Elementary",   callback_data="level_elementary"),
    )
    builder.row(
        InlineKeyboardButton(text="Intermediate", callback_data="level_intermediate"),
        InlineKeyboardButton(text="Advanced",     callback_data="level_advanced"),
    )
    builder.row(InlineKeyboardButton(text="← Back", callback_data="settings"))
    return builder.as_markup()


def get_persona_keyboard() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    personas = get_all_personas()
    for key, name in personas.items():
        builder.row(InlineKeyboardButton(text=name, callback_data=f"persona_{key}"))
    builder.row(InlineKeyboardButton(text="← Back", callback_data="settings"))
    return builder.as_markup()


def get_main_menu_keyboard(user_id: int = 0) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.row(InlineKeyboardButton(text="📖 How to use", callback_data="how_to_use"))
    builder.row(
        InlineKeyboardButton(text="📊 My Stats",      callback_data="my_stats"),
        InlineKeyboardButton(text="📚 My Vocabulary", callback_data="my_vocabulary"),
    )
    builder.row(InlineKeyboardButton(text="⚙️ Settings", callback_data="settings"))
    if user_id in ADMIN_IDS:
        builder.row(InlineKeyboardButton(text="🔧 Админ-панель", callback_data="admin_panel"))
    return builder.as_markup()


def get_stats_back_keyboard(message_id: int, mode: str) -> InlineKeyboardMarkup:
    """Кнопка 'оригинал' после перевода статистики."""
    builder = InlineKeyboardBuilder()
    builder.row(InlineKeyboardButton(
        text="↩ Original",
        callback_data=f"stats_original_{message_id}_{mode}"
    ))
    return builder.as_markup()


def get_admin_panel_keyboard() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.row(InlineKeyboardButton(text="📊 Статистика",       callback_data="admin_stats"))
    builder.row(InlineKeyboardButton(text="👥 Пользователи",     callback_data="admin_users"))
    builder.row(InlineKeyboardButton(text="📣 Broadcast",        callback_data="admin_broadcast"))
    builder.row(InlineKeyboardButton(text="← Назад",             callback_data="back_to_settings"))
    return builder.as_markup()


def get_admin_stats_keyboard() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.row(InlineKeyboardButton(text="← Назад", callback_data="admin_panel"))
    return builder.as_markup()


def get_admin_users_keyboard(users: list) -> InlineKeyboardMarkup:
    """Список пользователей кнопками."""
    builder = InlineKeyboardBuilder()
    for u in users:
        uid = u.get("telegram_id", 0)
        name = u.get("first_name") or u.get("username") or str(uid)
        username = u.get("username")
        label = f"👤 {name}" + (f" @{username}" if username else "")
        builder.row(InlineKeyboardButton(
            text=label[:60],
            callback_data=f"admin_user_{uid}"
        ))
    builder.row(InlineKeyboardButton(text="← Назад", callback_data="admin_panel"))
    return builder.as_markup()


def get_admin_user_card_keyboard(telegram_id: int) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.row(InlineKeyboardButton(text="← К списку", callback_data="admin_users"))
    return builder.as_markup()


def get_settings_keyboard(notifications_enabled: bool) -> InlineKeyboardMarkup:
    notif_text = "🔔 Notifications: ON" if notifications_enabled else "🔕 Notifications: OFF"
    builder = InlineKeyboardBuilder()
    builder.row(InlineKeyboardButton(text=notif_text,                  callback_data="toggle_notifications"))
    builder.row(InlineKeyboardButton(text="📊 Change Level",           callback_data="change_level"))
    builder.row(InlineKeyboardButton(text="✏️ Correction Sensitivity", callback_data="correction_rate"))
    builder.row(InlineKeyboardButton(text="← Back",                    callback_data="back_to_menu"))
    return builder.as_markup()


def get_correction_rate_keyboard(current_rate: int) -> InlineKeyboardMarkup:
    from src.modes import CORRECTION_RATE_RELAXED, CORRECTION_RATE_BALANCED, CORRECTION_RATE_STRICT
    options = [
        (CORRECTION_RATE_RELAXED,  "😌 Relaxed"),
        (CORRECTION_RATE_BALANCED, "⚖️ Balanced"),
        (CORRECTION_RATE_STRICT,   "🎯 Strict"),
    ]
    builder = InlineKeyboardBuilder()
    for rate, label in options:
        text = f"✓ {label}" if rate == current_rate else label
        builder.row(InlineKeyboardButton(text=text, callback_data=f"set_correction_{rate}"))
    builder.row(InlineKeyboardButton(text="← Back", callback_data="settings"))
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
        InlineKeyboardButton(text="📥 Export",    callback_data="vocab_export"),
    )
    builder.row(InlineKeyboardButton(text="← Back", callback_data="back_to_menu"))
    return builder.as_markup()


def get_translate_keyboard(message_id: int) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.row(InlineKeyboardButton(text="🌐 Translate", callback_data=f"translate_{message_id}"))
    return builder.as_markup()


def get_original_keyboard(message_id: int) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.row(InlineKeyboardButton(text="🔤 Original", callback_data=f"original_{message_id}"))
    return builder.as_markup()


# ─── Reply keyboards (постоянные кнопки внизу) ────────────────────────────

def get_mode_keyboard() -> ReplyKeyboardMarkup:
    """Три кнопки режимов — основное состояние"""
    builder = ReplyKeyboardBuilder()
    builder.row(
        KeyboardButton(text="🎓 Tutor"),
        KeyboardButton(text="✉️ PenFriend"),
        KeyboardButton(text="🎙 Flow"),
    )
    return builder.as_markup(resize_keyboard=True, persistent=True)


def get_flow_start_keyboard() -> ReplyKeyboardMarkup:
    """Алиас для обратной совместимости"""
    return get_mode_keyboard()


def get_tutor_keyboard() -> ReplyKeyboardMarkup:
    builder = ReplyKeyboardBuilder()
    builder.row(KeyboardButton(text="⏹ Stop Tutor"))
    return builder.as_markup(resize_keyboard=True, persistent=True)


def get_penfriend_keyboard() -> ReplyKeyboardMarkup:
    builder = ReplyKeyboardBuilder()
    builder.row(
        KeyboardButton(text="↩ Switch"),
        KeyboardButton(text="⏹ Stop PenFriend"),
    )
    return builder.as_markup(resize_keyboard=True, persistent=True)


def get_flow_stop_keyboard() -> ReplyKeyboardMarkup:
    builder = ReplyKeyboardBuilder()
    builder.row(
        KeyboardButton(text="↩ Switch"),
        KeyboardButton(text="⏹ Stop Flow"),
    )
    return builder.as_markup(resize_keyboard=True, persistent=True)


# ─── Flow / PenFriend inline кнопки ──────────────────────────────────────

def get_flow_voice_keyboard(message_id: int) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.row(
        InlineKeyboardButton(text="📝 Text",      callback_data=f"flow_text_{message_id}"),
        InlineKeyboardButton(text="🌐 Translate", callback_data=f"flow_translate_{message_id}"),
    )
    return builder.as_markup()


def get_flow_voice_text_keyboard(message_id: int) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.row(InlineKeyboardButton(text="🌐 Translate", callback_data=f"flow_translate_{message_id}"))
    return builder.as_markup()


def get_flow_voice_translate_keyboard(message_id: int) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.row(InlineKeyboardButton(text="🔤 Original", callback_data=f"flow_original_{message_id}"))
    return builder.as_markup()


def get_flow_user_voice_keyboard(message_id: int) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.row(
        InlineKeyboardButton(text="📝 Text",      callback_data=f"uvoice_text_{message_id}"),
        InlineKeyboardButton(text="🌐 Translate", callback_data=f"uvoice_translate_{message_id}"),
    )
    return builder.as_markup()
