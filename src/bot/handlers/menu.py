import logging
import html
from aiogram import Router, F
from aiogram.types import Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.filters import Command
from aiogram.utils.keyboard import InlineKeyboardBuilder

from src.bot.keyboards import get_level_keyboard
from src.services.supabase_db import db

router = Router()
logger = logging.getLogger(__name__)

# --- ОБРАБОТЧИКИ КОМАНД (Системное меню) ---

@router.message(Command("stats"))
async def cmd_stats(message: Message):
    """Показываем статистику пользователя"""
    try:
        stats = await db.get_user_stats(message.from_user.id)
        user = stats.get("user", {})
        
        level = str(user.get('level', 'Not set')).upper()
        streak = user.get('streak_days', 0)
        msgs = user.get('free_messages_used', 0)
        vocab_count = stats.get('vocabulary_count', 0)
        
        stats_text = (
            f"📊 <b>Your Speech Flow Stats</b>\n\n"
            f"👤 <b>Profile:</b>\n"
            f"• Level: <b>{level}</b>\n"
            f"• Streak: <b>{streak} days</b>\n"
            f"• Total messages: <b>{msgs}</b>\n\n"
            f"📈 <b>Progress:</b>\n"
            f"• Words in vocabulary: <b>{vocab_count}</b>\n\n"
            f"🎯 <b>Error analysis:</b>\n"
        )
        
        error_stats = stats.get("error_stats", {})
        if error_stats:
            for category, count in error_stats.items():
                cat_safe = html.escape(str(category))
                stats_text += f"• {cat_safe}: <b>{count}</b>\n"
        else:
            stats_text += "No errors logged yet. Keep practicing!"
        
        await message.answer(stats_text, parse_mode="HTML")
    except Exception as e:
        logger.error(f"Error showing stats: {e}")
        await message.answer("Error loading stats.", parse_mode="HTML")

@router.message(Command("vocabulary"))
async def cmd_vocabulary(message: Message):
    """Показываем словарь пользователя"""
    try:
        vocabulary = await db.get_user_vocabulary(message.from_user.id, limit=20)
        
        if not vocabulary:
            vocab_text = "📚 <b>Your Vocabulary</b>\n\nYour vocabulary is empty. New words from our conversations will appear here automatically."
        else:
            vocab_text = "📚 <b>Your Vocabulary (Last 20)</b>\n\n"
            for i, item in enumerate(vocabulary, 1):
                word = html.escape(item.get("word_or_phrase", ""))
                translation = html.escape(item.get("translation", ""))
                context = html.escape(item.get("context_sentence", ""))
                
                vocab_text += f"{i}. <b>{word}</b> - {translation}\n"
                if context:
                    vocab_text += f"   <i>\"{context[:50]}...\"</i>\n"
                vocab_text += "\n"
        
        await message.answer(vocab_text, parse_mode="HTML")
    except Exception as e:
        logger.error(f"Error showing vocabulary: {e}")
        await message.answer("Error loading vocabulary.", parse_mode="HTML")

@router.message(Command("voice"))
async def cmd_voice(message: Message):
    """Меню выбора голоса"""
    builder = InlineKeyboardBuilder()
    
    # Голоса Groq Orpheus (если у тебя работают они). Если OpenAI - поменяй на alloy, echo и тд.
    voices = ["austin", "daniel", "troy", "autumn", "diana", "hannah"]
    
    for v in voices:
        builder.row(InlineKeyboardButton(text=v.capitalize(), callback_data=f"setvoice_{v}"))
    
    await message.answer("🗣 <b>Choose your AI tutor's voice:</b>", reply_markup=builder.as_markup(), parse_mode="HTML")

@router.message(Command("author"))
async def cmd_author(message: Message):
    """Информация об авторе"""
    await message.answer(
        "👨‍💻 <b>SpeechFlow AI</b>\n\n"
        "Created by: @inikonoff\n"
        "Feedback and suggestions are welcome!",
        parse_mode="HTML"
    )

# --- ОБРАБОТЧИКИ КНОПОК (Callbacks) ---

@router.callback_query(F.data.startswith("setvoice_"))
async def process_voice_selection(callback: CallbackQuery):
    """Обработка выбора голоса"""
    voice_name = callback.data.split("_")[1]
    user_id = callback.from_user.id
    
    try:
        # Обновляем в БД
        await db.update_user_voice(user_id, voice_name)
        await callback.message.edit_text(f"✅ Voice successfully changed to <b>{voice_name.capitalize()}</b>!", parse_mode="HTML")
    except Exception as e:
        logger.error(f"Error setting voice: {e}")
        await callback.answer("Error changing voice.", show_alert=True)

@router.callback_query(F.data == "how_to_use")
async def show_how_to_use(callback: CallbackQuery):
    how_to_text = """🗣 <b>How to use Speech Flow AI</b>

1. <b>Just speak/write in English</b> – I'll analyze your speech for errors
2. <b>Natural conversation</b> – I'll keep the dialogue flowing naturally
3. <b>Integrated corrections</b> – Mistakes are corrected within our conversation
4. <b>Vocabulary building</b> – New words are automatically added to your personal dictionary
5. <b>Voice messages recommended</b> – Speaking practice is key for fluency!

💡 <b>Tips:</b>
• Don't worry about mistakes – that's how we learn
• Try to use new words in our conversations
• Check your stats regularly to track progress"""
    
    await callback.message.edit_text(how_to_text, parse_mode="HTML")
    await callback.answer()

@router.callback_query(F.data == "change_level")
async def change_user_level(callback: CallbackQuery):
    await callback.message.edit_text(
        "<b>Select your new English level:</b>",
        reply_markup=get_level_keyboard(),
        parse_mode="HTML"
    )
    await callback.answer()
# ... (твой остальной код в menu.py) ...

@router.callback_query(F.data == "my_stats")
async def cq_show_stats(callback: CallbackQuery):
    """Обработка inline-кнопки My Stats"""
    try:
        stats = await db.get_user_stats(callback.from_user.id)
        user = stats.get("user", {})
        
        level = str(user.get('level', 'Not set')).upper()
        streak = user.get('streak_days', 0)
        vocab_count = stats.get('vocabulary_count', 0)
        
        stats_text = (
            f"📊 <b>Your Speech Flow Stats</b>\n\n"
            f"👤 <b>Profile:</b>\n"
            f"• Level: <b>{level}</b>\n"
            f"• Streak: <b>{streak} days</b>\n\n"
            f"📈 <b>Progress:</b>\n"
            f"• Words in vocabulary: <b>{vocab_count}</b>\n\n"
            f"🎯 <b>Error analysis:</b>\n"
        )
        
        error_stats = stats.get("error_stats", {})
        if error_stats:
            for category, count in error_stats.items():
                cat_safe = html.escape(str(category))
                stats_text += f"• {cat_safe}: <b>{count}</b>\n"
        else:
            stats_text += "No errors logged yet. Keep practicing!"
        
        # Изменяем текущее сообщение с кнопками на текст статистики
        await callback.message.edit_text(stats_text, parse_mode="HTML")
        await callback.answer()
    except Exception as e:
        logger.error(f"Error in cq_show_stats: {e}")
        await callback.answer("Error loading stats.", show_alert=True)

@router.callback_query(F.data == "my_vocabulary")
async def cq_show_vocab(callback: CallbackQuery):
    """Обработка inline-кнопки My Vocabulary"""
    try:
        vocabulary = await db.get_user_vocabulary(callback.from_user.id, limit=20)
        
        if not vocabulary:
            vocab_text = "📚 <b>Your Vocabulary</b>\n\nYour vocabulary is empty. New words from our conversations will appear here automatically."
        else:
            vocab_text = "📚 <b>Your Vocabulary (Last 20)</b>\n\n"
            for i, item in enumerate(vocabulary, 1):
                word = html.escape(item.get("word_or_phrase", ""))
                translation = html.escape(item.get("translation", ""))
                context = html.escape(item.get("context_sentence", ""))
                
                vocab_text += f"{i}. <b>{word}</b> - {translation}\n"
                if context:
                    vocab_text += f"   <i>\"{context[:50]}...\"</i>\n"
                vocab_text += "\n"
        
        await callback.message.edit_text(vocab_text, parse_mode="HTML")
        await callback.answer()
    except Exception as e:
        logger.error(f"Error in cq_show_vocab: {e}")
        await callback.answer("Error loading vocabulary.", show_alert=True)
