import logging
from aiogram import Router, F
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.types import Message, CallbackQuery

from src.bot.keyboards import (
    get_level_keyboard,
    get_persona_keyboard,
    get_flow_start_keyboard,
)
from src.services.supabase_db import db

router = Router()
logger = logging.getLogger(__name__)


@router.message(Command("start"))
async def cmd_start(message: Message, state: FSMContext):
    try:
        await db.get_or_create_user(
            telegram_id=message.from_user.id,
            username=message.from_user.username
        )

        # Сбрасываем FSM — на случай если был Flow Mode
        await state.clear()

        # Постоянная Reply-кнопка Flow
        await message.answer(".", reply_markup=get_flow_start_keyboard())

        await message.answer(
            "👋 Welcome to <b>Speech Flow AI</b>!\n\n"
            "I'm your AI English tutor focused on <b>conversational fluency</b>.\n\n"
            "To get started, please select your English level:",
            parse_mode="HTML",
            reply_markup=get_level_keyboard()
        )

    except Exception as e:
        logger.error(f"Error in /start: {e}")
        await message.answer("An error occurred. Please try again.")


@router.callback_query(F.data.startswith("level_"))
async def process_level_and_ask_persona(callback: CallbackQuery, state: FSMContext):
    """После выбора уровня — сохраняем и предлагаем выбрать собеседника"""
    try:
        level = callback.data.split("_")[1]
        await db.update_user_level(callback.from_user.id, level)

        # Выставляем FSM состояние choosing_persona —
        # дальнейший выбор персонажа обработает flow_persona_selected в message.py
        from src.bot.handlers.message import FlowState
        await state.set_state(FlowState.choosing_persona)

        await callback.message.edit_text(
            f"✅ Level set to <b>{level.upper()}</b>.\n\n"
            "Now, <b>who would you like to talk to?</b>\n\n"
            "Each person has their own story, personality, and way of talking. "
            "You can switch anytime.",
            parse_mode="HTML",
            reply_markup=get_persona_keyboard()
        )
        await callback.answer()

    except Exception as e:
        logger.error(f"Error in level selection: {e}")
        await callback.answer("An error occurred.", show_alert=True)
