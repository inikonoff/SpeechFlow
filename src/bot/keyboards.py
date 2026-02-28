from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton, ReplyKeyboardMarkup, KeyboardButton
from aiogram.utils.keyboard import InlineKeyboardBuilder, ReplyKeyboardBuilder


def get_level_keyboard() -> InlineKeyboardMarkup:
    """Клавиатура для выбора уровня"""
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


def get_main_menu_keyboard() -> InlineKeyboardMarkup:
    """Основное меню после выбора уровня"""
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
    """Кнопка 'Назад в меню'"""
    builder = InlineKeyboardBuilder()
    builder.row(InlineKeyboardButton(text="← Back to Menu", callback_data="back_to_menu"))
    return builder.as_markup()


def get_vocabulary_actions_keyboard() -> InlineKeyboardMarkup:
    """Действия со словарем"""
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
    """Кнопка перевода под ответом бота"""
    builder = InlineKeyboardBuilder()
    builder.row(
        InlineKeyboardButton(text="🌐 Translate", callback_data=f"translate_{message_id}")
    )
    return builder.as_markup()


def get_original_keyboard(message_id: int) -> InlineKeyboardMarkup:
    """Кнопка возврата к оригиналу"""
    builder = InlineKeyboardBuilder()
    builder.row(
        InlineKeyboardButton(text="🔤 Original", callback_data=f"original_{message_id}")
    )
    return builder.as_markup()


# ─── Flow Mode ─────────────────────────────────────────────────────────────

def get_flow_start_keyboard() -> ReplyKeyboardMarkup:
    """Reply-кнопка для активации Flow Mode (всегда видна)"""
    builder = ReplyKeyboardBuilder()
    builder.row(KeyboardButton(text="▶ Flow"))
    return builder.as_markup(resize_keyboard=True, persistent=True)


def get_flow_stop_keyboard() -> ReplyKeyboardMarkup:
    """Reply-кнопка для выхода из Flow Mode"""
    builder = ReplyKeyboardBuilder()
    builder.row(KeyboardButton(text="⏹ Stop Flow"))
    return builder.as_markup(resize_keyboard=True, persistent=True)
