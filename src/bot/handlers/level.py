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
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, BufferedInputFile

from src.bot.handlers.states import LevelChangeState
from src.bot.keyboards import get_settings_keyboard, get_level_select_keyboard, get_main_menu_keyboard
from src.config import (
    ONBOARDING_VOICE_BEGINNER,
    ONBOARDING_VOICE_INTERMEDIATE,
    ONBOARDING_VOICE_ADVANCED,
    ONBOARDING_SPOILERS,
)
from src.services.supabase_db import db
from src.services.groq_client import groq_client
from src.personas import get_persona_voice
from src.utils.tg_helpers import safe_edit_text

router = Router()
logger = logging.getLogger(__name__)

# ─── Порядок уровней для определения повышение/понижение ──────────────────────

LEVEL_ORDER = ["beginner", "elementary", "intermediate", "advanced"]

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
        InlineKeyboardButton(text="Elementary",   callback_data="setlevel_elementary"),
    )
    builder.row(
        InlineKeyboardButton(text="Intermediate", callback_data="setlevel_intermediate"),
        InlineKeyboardButton(text="Advanced",     callback_data="setlevel_advanced"),
    )
    builder.row(InlineKeyboardButton(text="← Back", callback_data="settings"))
    return builder.as_markup()

# ─── Хелпер: спойлер под голосовым ───────────────────────────────────────────

def _make_spoiler(key: str) -> str:
    data = ONBOARDING_SPOILERS.get(key, {})
    en = html.escape(data.get("en", ""))
    ru = html.escape(data.get("ru", ""))
    return f"<blockquote expandable>{en}\n\n{ru}</blockquote>"

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
        old_level = user.get("level", "intermediate")

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

        # Голосовое онбординга для нового уровня (захардкоженное)
        voice_map = {
            "beginner":     (ONBOARDING_VOICE_BEGINNER,     "beginner"),
            "intermediate": (ONBOARDING_VOICE_INTERMEDIATE, "intermediate"),
            "advanced":     (ONBOARDING_VOICE_ADVANCED,     "advanced"),
        }
        logger.info(f"Voice map lookup: new_level={new_level!r}, available keys={list(voice_map.keys())}")
        file_id, spoiler_key = voice_map.get(new_level, (ONBOARDING_VOICE_INTERMEDIATE, "intermediate"))
        logger.info(f"Selected file_id={file_id!r}, spoiler_key={spoiler_key!r}")

        if file_id:
            await callback.message.answer_voice(file_id)
        else:
            logger.warning(f"Onboarding voice file_id for '{spoiler_key}' is empty")

        await callback.message.answer(_make_spoiler(spoiler_key), parse_mode="HTML")

        # Возвращаем в Settings
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
    """После смены уровня возвращаем в главное меню с обновлённым уровнем."""
    level = user.get("level", "")
    await callback.message.answer(
        "🏠 <b>Main Menu</b>",
        parse_mode="HTML",
        reply_markup=get_main_menu_keyboard(callback.from_user.id, level)
    )
