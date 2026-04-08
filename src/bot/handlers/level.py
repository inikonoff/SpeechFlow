"""
SpeechFlow Pro — Level Change Handler

Смена уровня через Settings:
- Повышение → TTS реакция Mrs. Smith (1 раз) + онбординг-голосовое нового уровня
- Понижение → только текстовое уведомление
- Тот же уровень → тихое подтверждение
"""

import logging
import html
from aiogram import Router, F
from aiogram.filters import Command, StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, BufferedInputFile

from src.bot.handlers.states import LevelChangeState
from src.bot.keyboards import get_level_select_keyboard
from src.services.supabase_db import db
from src.services.groq_client import groq_client
from src.personas import get_persona_voice
from src.utils.tg_helpers import safe_edit_text

router = Router()
logger = logging.getLogger(__name__)

# ─── Порядок уровней для определения повышение/понижение ──────────────────────

LEVEL_ORDER = ["beginner", "intermediate", "advanced"]

def _level_rank(level: str) -> int:
    try:
        return LEVEL_ORDER.index(level.lower())
    except ValueError:
        return 1  # intermediate как дефолт

# ─── Клавиатура смены уровня (без кнопки Back к онбордингу) ──────────────────

def get_change_level_keyboard():
    from aiogram.utils.keyboard import InlineKeyboardBuilder
    from aiogram.types import InlineKeyboardButton
    builder = InlineKeyboardBuilder()
    builder.row(
        InlineKeyboardButton(text="Beginner",     callback_data="setlevel_beginner"),
        InlineKeyboardButton(text="Intermediate", callback_data="setlevel_intermediate"),
        InlineKeyboardButton(text="Advanced",     callback_data="setlevel_advanced"),
    )
    builder.row(InlineKeyboardButton(text="← Back", callback_data="settings"))
    return builder.as_markup()

# ─── /level команда ──────────────────────────────────────────────────────────

@router.message(Command("level"), StateFilter("*"))
async def cmd_level(message, state: FSMContext):
    """Команда /level — показывает выбор уровня прямо в чате."""
    try:
        from aiogram.types import Message
        await state.clear()
        user = await db.get_or_create_user(message.from_user.id)
        current_level = user.get("level", "")
        await message.answer(
            "📚 <b>Change your English level</b>\n\nChoose the level that feels right:",
            parse_mode="HTML",
            reply_markup=get_level_select_keyboard(current_level)
        )
        await state.set_state(LevelChangeState.choosing_level)
    except Exception as e:
        logger.error(f"Error in /level: {e}")
        await message.answer("Something went wrong. Please try again.")

# ─── Триггер из Settings ──────────────────────────────────────────────────────

@router.callback_query(F.data == "change_level")
async def cq_change_level(callback: CallbackQuery, state: FSMContext):
    """Кнопка 'Change Level' в главном меню → показываем выбор уровня с текущим уровнем."""
    try:
        user = await db.get_or_create_user(callback.from_user.id)
        current_level = user.get("level", "")
        await safe_edit_text(
            callback.message,
            "📚 <b>Change your English level</b>\n\nChoose the level that feels right:",
            parse_mode="HTML",
            reply_markup=get_level_select_keyboard(current_level)
        )
        await state.set_state(LevelChangeState.choosing_level)
        await callback.answer()
    except Exception as e:
        logger.error(f"Error in cq_change_level: {e}")
        await callback.answer("Error.", show_alert=True)

# ─── Выбор нового уровня ──────────────────────────────────────────────────────

@router.callback_query(F.data.startswith("setlevel_"))
async def cq_set_level(callback: CallbackQuery, state: FSMContext):
    """Пользователь выбрал новый уровень."""
    try:
        parts = callback.data.split("_")  # setlevel_intermediate → ["setlevel", "intermediate"]
        new_level = "_".join(parts[1:])  # на случай если уровень содержит _ (не содержит, но надёжнее)
        logger.info(f"Level change requested: callback_data={callback.data!r}, new_level={new_level!r}")
        user_id = callback.from_user.id

        user = await db.get_or_create_user(user_id)
        # Сохраняем old_level сразу — до state.clear() и любых других операций с БД
        old_level = user.get("level", "intermediate")
        logger.info(f"Level change: old_level={old_level!r}, new_level={new_level!r}")

        old_rank = _level_rank(old_level)
        new_rank = _level_rank(new_level)

        await state.clear()

        if new_level == old_level:
            # Тот же уровень — тихое подтверждение
            await callback.answer(f"Already at {new_level.capitalize()} level.", show_alert=False)
            await _back_to_settings(callback, user)
            return

        # Сохраняем новый уровень
        await db.update_user_level(user_id, new_level)

        if new_rank > old_rank:
            # Повышение → TTS реакция Mrs. Smith + голосовое онбординга
            await _handle_upgrade(callback, old_level, new_level)
        else:
            # Понижение → только текст, без осуждения
            await _handle_downgrade(callback, old_level, new_level, user)

    except Exception as e:
        logger.error(f"Error in cq_set_level: {e}")
        await callback.answer("Error.", show_alert=True)

# ─── Повышение уровня ─────────────────────────────────────────────────────────

async def _handle_upgrade(callback: CallbackQuery, old_level: str, new_level: str):
    """
    Повышение: TTS реакция Mrs. Smith (~2 предложения) +
    голосовое онбординга для нового уровня + спойлер.
    """
    try:
        await callback.message.edit_reply_markup(reply_markup=None)
        await callback.answer()

        # Генерируем живую реакцию Mrs. Smith через LLM
        reaction_text = await groq_client.generate_level_change_reaction(old_level, new_level)

        # Отправляем TTS реакцию голосом diana (Mrs. Smith)
        voice_bytes = await groq_client.text_to_speech(reaction_text, voice="diana")
        if voice_bytes:
            voice_file = BufferedInputFile(voice_bytes, filename="reaction.wav")
            await callback.message.answer_voice(voice_file)
        else:
            # Fallback — текст если TTS не сработал
            await callback.message.answer(
                f"📚 <i>{html.escape(reaction_text)}</i>",
                parse_mode="HTML"
            )

        # Возвращаем в меню
        user = await db.get_or_create_user(callback.from_user.id)
        await _back_to_settings(callback, user)

    except Exception as e:
        logger.error(f"Error in _handle_upgrade: {e}")
        await callback.message.answer("Something went wrong during level upgrade.")

# ─── Понижение уровня ─────────────────────────────────────────────────────────

async def _handle_downgrade(callback: CallbackQuery, old_level: str, new_level: str, user: dict):
    """Понижение: только текстовое уведомление, без осуждения."""
    try:
        await callback.message.edit_reply_markup(reply_markup=None)
        await callback.answer()

        level_label = new_level.capitalize()
        await callback.message.answer(
            f"📚 Level set to <b>{html.escape(level_label)}</b>.\n\n"
            f"We'll adjust the pace. No pressure.",
            parse_mode="HTML"
        )

        await _back_to_settings(callback, user)

    except Exception as e:
        logger.error(f"Error in _handle_downgrade: {e}")

# ─── Хелпер: вернуться в Settings ────────────────────────────────────────────

async def _back_to_settings(callback: CallbackQuery, user: dict):
    """Тихое подтверждение после смены уровня — без лишних меню."""
    pass  # ничего не показываем — реакция Mrs. Smith уже была
