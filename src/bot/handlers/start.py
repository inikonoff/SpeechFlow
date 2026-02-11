import logging
from aiogram import Router, types
from aiogram.filters import Command
from aiogram.types import Message

from src.bot.keyboards import get_level_keyboard
from src.services.supabase_db import db

router = Router()
logger = logging.getLogger(__name__)


@router.message(Command("start"))
async def cmd_start(message: Message):
    """Обработчик команды /start"""
    try:
        # Получаем или создаем пользователя
        user = await db.get_or_create_user(
            telegram_id=message.from_user.id,
            username=message.from_user.username
        )
        
        # Приветственное сообщение
        welcome_text = """👋 Welcome to *Speech Flow AI*!

I'm your AI English tutor focused on *conversational fluency*. 

To get started, please select your English level:"""

        await message.answer(
            welcome_text,
            parse_mode="Markdown",
            reply_markup=get_level_keyboard()
        )
        
    except Exception as e:
        logger.error(f"Error in /start: {e}")
        await message.answer("An error occurred. Please try again.")
