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
    """Выбор собеседника — показывается после выбора уровня и при Switch"""
    builder = InlineKeyboardBuilder()
    personas = get_all_personas()  # {key: display_name}
    for key, name in personas.items():
        builder.row(InlineKeyboardButton(text=name, callback_data=f"persona_{key}"))
    return builder.as_markup()


def get_main_menu_keyboard() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.row(
        InlineKeyboardButton(text="📖 How to use Speech Flow", callback_data="how_to_use"),
    )
    builder.row(
        InlineKeyboardButton(text="📊 My Stats", callback_data="my_stats"),
        InlineKeyboardButton(text="📚 My Vocabulary", callback_data="my_vocabulary"),
    )
    builder.row(
        InlineKeyboardButton(text="⚙️ Change Level", callback_data="change_level"),
    )
    return builder.as_markup()


def get_back_to_menu_keyboard() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.row(InlineKeyboardButton(text="← Back to Menu", callback_data="back_to_menu"))
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
    """Reply-кнопка активации Flow Mode — всегда видна"""
    builder = ReplyKeyboardBuilder()
    builder.row(KeyboardButton(text="▶ Flow"))
    return builder.as_markup(resize_keyboard=True, persistent=True)


def get_flow_stop_keyboard() -> ReplyKeyboardMarkup:
    """Reply-кнопка выхода из Flow Mode"""
    builder = ReplyKeyboardBuilder()
    builder.row(KeyboardButton(text="⏹ Stop Flow"))
    return builder.as_markup(resize_keyboard=True, persistent=True)


def get_flow_active_keyboard(persona_name: str) -> InlineKeyboardMarkup:
    """Inline-кнопка Switch во время активного Flow диалога"""
    builder = InlineKeyboardBuilder()
    builder.row(
        InlineKeyboardButton(
            text=f"↩ Switch ({persona_name})",
            callback_data="flow_switch"
        )
    )
    return builder.as_markup()
