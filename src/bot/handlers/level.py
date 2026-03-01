import logging
from aiogram import Router, F
from aiogram.types import CallbackQuery

from src.bot.keyboards import get_persona_keyboard
from src.services.supabase_db import db

router = Router()
logger = logging.getLogger(__name__)


@router.callback_query(F.data.startswith("level_"))
async def process_level_selection(callback: CallbackQuery):
    """
    Обработчик смены уровня через меню (Change Level).
    Онбординг нового пользователя — в start.py.
    """
    try:
        level = callback.data.split("_")[1]
        success = await db.update_user_level(callback.from_user.id, level)

        if success:
            await callback.message.edit_text(
                f"✅ Level updated to <b>{level.upper()}</b>.\n\n"
                f"Would you like to switch your conversation partner too?",
                parse_mode="HTML",
                reply_markup=get_persona_keyboard()
            )
        else:
            await callback.answer("Error updating level. Try again.", show_alert=True)

        await callback.answer()

    except Exception as e:
        logger.error(f"Error in level selection: {e}")
        await callback.answer("An error occurred.", show_alert=True)
