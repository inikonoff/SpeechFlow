import logging
from aiogram import Router, types
from aiogram.types import CallbackQuery

from src.bot.keyboards import get_main_menu_keyboard
from src.services.supabase_db import db

router = Router()
logger = logging.getLogger(__name__)


@router.callback_query(lambda c: c.data.startswith("level_"))
async def process_level_selection(callback: CallbackQuery):
    """Обработчик выбора уровня"""
    try:
        # Извлекаем уровень из callback_data
        level = callback.data.split("_")[1]  # level_beginner -> beginner
        
        # Обновляем уровень в БД
        success = await db.update_user_level(callback.from_user.id, level)
        
        if success:
            response_text = f"""✅ Your level is set to *{level.upper()}*.

Perfect! We can start chatting right now. 

💡 *Speech Flow features:*
• Real-time grammar corrections
• Natural conversation flow  
• Vocabulary tracking
• Progress analytics

Just send me a message in English (text or voice)!"""
            
            await callback.message.edit_text(
                response_text,
                parse_mode="Markdown",
                reply_markup=get_main_menu_keyboard()
            )
        else:
            await callback.answer("Error setting level. Try again.", show_alert=True)
        
        await callback.answer()
        
    except Exception as e:
        logger.error(f"Error in level selection: {e}")
        await callback.answer("An error occurred.", show_alert=True)
