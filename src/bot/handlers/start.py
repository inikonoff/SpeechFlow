import logging
from aiogram import Router, F
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.types import Message, CallbackQuery

from src.bot.keyboards import (
    get_level_keyboard,
    get_persona_keyboard,
    get_mode_keyboard,
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

        await state.clear()

        await message.answer(
            "Welcome to Speech Flow AI!

"
            "To get started, please select your English level:",
            parse_mode="HTML",
            reply_markup=get_level_keyboard()
        )

    except Exception as e:
        logger.error(f"Error in /start: {e}")
        await message.answer("An error occurred. Please try again.")


@router.callback_query(F.data.startswith("level_"))
async def process_level_and_ask_persona(callback: CallbackQuery, state: FSMContext):
    try:
        level = callback.data.split("_")[1]
        await db.update_user_level(callback.from_user.id, level)

        await callback.message.edit_text(
            f"Level set to {level.upper()}. Now choose your mode:",
            parse_mode="HTML",
        )

        await callback.message.answer(
            "Tutor: corrections and explanations
"
            "PenFriend: text chat with a character
"
            "Flow: pure voice conversation",
            parse_mode="HTML",
            reply_markup=get_mode_keyboard()
        )

        await callback.answer()

    except Exception as e:
        logger.error(f"Error in level selection: {e}")
        await callback.answer("An error occurred.", show_alert=True)
