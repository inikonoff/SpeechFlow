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


def get_main_menu_keyboard(user_id: int = 0, level: str = "") -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.row(InlineKeyboardButton(text="📖 How to use", callback_data="how_to_use"))
    builder.row(InlineKeyboardButton(text="📊 My Stats",   callback_data="my_stats"))
    level_label = f"📚 My Level: {level.capitalize()}" if level else "📚 Change Level"
    builder.row(InlineKeyboardButton(text=level_label, callback_data="change_level"))
    builder.row(InlineKeyboardButton(text="⚙️ Settings",   callback_data="settings"))
    if user_id in ADMIN_IDS:
        builder.row(InlineKeyboardButton(text="🔧 Админ-панель", callback_data="admin_panel"))
    return builder.as_markup()


def get_stats_back_keyboard(message_id: int, mode: str) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.row(InlineKeyboardButton(
        text="↩ Original",
        callback_data=f"stats_original_{message_id}_{mode}"
    ))
    return builder.as_markup()


def get_admin_panel_keyboard() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.row(InlineKeyboardButton(text="📊 Статистика",    callback_data="admin_stats"))
    builder.row(InlineKeyboardButton(text="👥 Пользователи", callback_data="admin_users"))
    builder.row(InlineKeyboardButton(text="📣 Broadcast",    callback_data="admin_broadcast"))
    builder.row(InlineKeyboardButton(text="← Назад",         callback_data="back_to_settings"))
    return builder.as_markup()


def get_admin_stats_keyboard() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.row(InlineKeyboardButton(text="← Назад", callback_data="admin_panel"))
    return builder.as_markup()


def get_admin_users_keyboard(users: list) -> InlineKeyboardMarkup:
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


def get_settings_keyboard(notifications_enabled: bool, recasting_enabled: bool, mistakes_practice_enabled: bool = False, user_id: int = 0) -> InlineKeyboardMarkup:
    notif_text    = "🔔 Notifications: ON"      if notifications_enabled      else "🔕 Notifications: OFF"
    recast_text   = "📝 Recasting: ON"          if recasting_enabled          else "📝 Recasting: OFF"
    practice_text = "🎯 Mistakes Practice: ON"  if mistakes_practice_enabled  else "🎯 Mistakes Practice: OFF"

    builder = InlineKeyboardBuilder()
    builder.row(InlineKeyboardButton(text=notif_text,    callback_data="toggle_notifications"))
    builder.row(InlineKeyboardButton(text=recast_text,   callback_data="toggle_recasting"))
    builder.row(InlineKeyboardButton(text=practice_text, callback_data="toggle_mistakes_practice"))
    if user_id in ADMIN_IDS:
        builder.row(InlineKeyboardButton(text="🔧 Админ-панель", callback_data="admin_panel"))
    builder.row(InlineKeyboardButton(text="← Back", callback_data="back_to_menu"))
    return builder.as_markup()


def get_level_select_keyboard(current_level: str = "") -> InlineKeyboardMarkup:
    """Выбор уровня из главного меню — Back возвращает в главное меню, не в Settings."""
    def label(lvl: str, text: str) -> str:
        return f"✓ {text}" if lvl == current_level else text
    builder = InlineKeyboardBuilder()
    builder.row(
        InlineKeyboardButton(text=label("beginner", "Beginner"),       callback_data="setlevel_beginner"),
        InlineKeyboardButton(text=label("elementary", "Elementary"),   callback_data="setlevel_elementary"),
    )
    builder.row(
        InlineKeyboardButton(text=label("intermediate", "Intermediate"), callback_data="setlevel_intermediate"),
        InlineKeyboardButton(text=label("advanced", "Advanced"),         callback_data="setlevel_advanced"),
    )
    builder.row(InlineKeyboardButton(text="← Back", callback_data="back_to_menu"))
    return builder.as_markup()


def get_back_to_menu_keyboard() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.row(InlineKeyboardButton(text="← Back to Menu", callback_data="back_to_menu"))
    return builder.as_markup()


def get_back_to_settings_keyboard() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.row(InlineKeyboardButton(text="← Back to Settings", callback_data="settings"))
    return builder.as_markup()


def get_translate_keyboard(message_id: int) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.row(InlineKeyboardButton(text="🌐 Translate", callback_data=f"translate_{message_id}"))
    return builder.as_markup()


def get_original_keyboard(message_id: int) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.row(InlineKeyboardButton(text="🔤 Original", callback_data=f"original_{message_id}"))
    return builder.as_markup()


# ─── Reply keyboards ──────────────────────────────────────────────────────

def get_mode_keyboard() -> ReplyKeyboardMarkup:
    builder = ReplyKeyboardBuilder()
    builder.row(
        KeyboardButton(text="🎓 Tutor"),
        KeyboardButton(text="✉️ PenFriend"),
        KeyboardButton(text="🎙 Flow"),
    )
    return builder.as_markup(resize_keyboard=True, persistent=True)


def get_flow_start_keyboard() -> ReplyKeyboardMarkup:
    """Алиас для обратной совместимости."""
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
    """Кнопки под голосовым сообщением пользователя в Flow Mode."""
    builder = InlineKeyboardBuilder()
    builder.row(
        InlineKeyboardButton(text="📝 Text",      callback_data=f"uvoice_text_{message_id}"),
        InlineKeyboardButton(text="🌐 Translate", callback_data=f"uvoice_translate_{message_id}"),
    )
    return builder.as_markup()

def get_flow_user_voice_text_keyboard(message_id: int) -> InlineKeyboardMarkup:
    """После показа текста голосового юзера — только Translate."""
    builder = InlineKeyboardBuilder()
    builder.row(
        InlineKeyboardButton(text="🌐 Translate", callback_data=f"uvoice_translate_{message_id}"),
    )
    return builder.as_markup()

def get_flow_user_voice_translate_keyboard(message_id: int) -> InlineKeyboardMarkup:
    """После перевода голосового юзера — кнопка Original."""
    builder = InlineKeyboardBuilder()
    builder.row(
        InlineKeyboardButton(text="🔤 Original", callback_data=f"uvoice_original_{message_id}"),
    )
    return builder.as_markup()
