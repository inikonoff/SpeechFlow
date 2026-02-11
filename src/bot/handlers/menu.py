import logging
from aiogram import Router, types
from aiogram.types import CallbackQuery

from src.bot.keyboards import get_main_menu_keyboard, get_back_to_menu_keyboard, get_level_keyboard
from src.services.supabase_db import db

router = Router()
logger = logging.getLogger(__name__)


@router.callback_query(lambda c: c.data == "how_to_use")
async def show_how_to_use(callback: CallbackQuery):
    """Показываем инструкцию по использованию Speech Flow"""
    how_to_text = """🗣 *How to use Speech Flow AI*

1. *Just speak/write in English* – I'll analyze your speech for errors
2. *Natural conversation* – I'll keep the dialogue flowing naturally
3. *Integrated corrections* – Mistakes are corrected within our conversation
4. *Vocabulary building* – New words are automatically added to your personal dictionary
5. *Voice messages recommended* – Speaking practice is key for fluency!

💡 *Tips:*
• Don't worry about mistakes – that's how we learn
• Try to use new words in our conversations
• Check your stats regularly to track progress

Ready to start? Just send me a message in English!"""
    
    await callback.message.edit_text(
        how_to_text,
        parse_mode="Markdown",
        reply_markup=get_back_to_menu_keyboard()
    )
    await callback.answer()


@router.callback_query(lambda c: c.data == "my_stats")
async def show_user_stats(callback: CallbackQuery):
    """Показываем статистику пользователя"""
    try:
        stats = await db.get_user_stats(callback.from_user.id)
        user = stats.get("user", {})
        
        stats_text = f"""📊 *Your Speech Flow Stats*

👤 *Profile:*
• Level: *{user.get('level', 'Not set').upper()}*
• Streak: *{user.get('streak_days', 0)} days*
• Total messages: *{user.get('free_messages_used', 0)}*

📈 *Progress:*
• Words in vocabulary: *{stats.get('vocabulary_count', 0)}*
• Total tokens used: *{user.get('total_tokens_used', 0)}*

🎯 *Error analysis:*
"""
        
        error_stats = stats.get("error_stats", {})
        if error_stats:
            for category, count in error_stats.items():
                stats_text += f"• {category}: *{count}*\n"
        else:
            stats_text += "No errors logged yet. Keep practicing!"
        
        await callback.message.edit_text(
            stats_text,
            parse_mode="Markdown",
            reply_markup=get_back_to_menu_keyboard()
        )
        await callback.answer()
        
    except Exception as e:
        logger.error(f"Error showing stats: {e}")
        await callback.answer("Error loading stats.", show_alert=True)


@router.callback_query(lambda c: c.data == "my_vocabulary")
async def show_user_vocabulary(callback: CallbackQuery):
    """Показываем словарь пользователя"""
    try:
        vocabulary = await db.get_user_vocabulary(callback.from_user.id, limit=20)
        
        if not vocabulary:
            vocab_text = "📚 *Your Vocabulary*\n\nYour vocabulary is empty. New words from our conversations will appear here automatically."
        else:
            vocab_text = "📚 *Your Vocabulary*\n\n"
            for i, item in enumerate(vocabulary, 1):
                word = item.get("word_or_phrase", "")
                translation = item.get("translation", "")
                context = item.get("context_sentence", "")
                
                vocab_text += f"{i}. *{word}* - {translation}\n"
                if context:
                    vocab_text += f"   _\"{context[:50]}...\"_\n"
                vocab_text += "\n"
        
        from src.bot.keyboards import get_vocabulary_actions_keyboard
        await callback.message.edit_text(
            vocab_text,
            parse_mode="Markdown",
            reply_markup=get_vocabulary_actions_keyboard()
        )
        await callback.answer()
        
    except Exception as e:
        logger.error(f"Error showing vocabulary: {e}")
        await callback.answer("Error loading vocabulary.", show_alert=True)


@router.callback_query(lambda c: c.data == "change_level")
async def change_user_level(callback: CallbackQuery):
    """Изменение уровня пользователя"""
    await callback.message.edit_text(
        "Select your new English level:",
        reply_markup=get_level_keyboard()
    )
    await callback.answer()


@router.callback_query(lambda c: c.data == "back_to_menu")
async def back_to_main_menu(callback: CallbackQuery):
    """Возврат в главное меню"""
    try:
        user = await db.get_or_create_user(callback.from_user.id)
        
        menu_text = f"""🏠 *Main Menu*

Your current level: *{user.get('level', 'Not set').upper()}*
Streak: *{user.get('streak_days', 0)} days*

Select an option below:"""
        
        await callback.message.edit_text(
            menu_text,
            parse_mode="Markdown",
            reply_markup=get_main_menu_keyboard()
        )
        await callback.answer()
        
    except Exception as e:
        logger.error(f"Error returning to menu: {e}")
        await callback.answer("Error.", show_alert=True)
