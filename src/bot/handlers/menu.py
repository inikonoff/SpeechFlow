import logging
import html
import re
from typing import Dict
from aiogram import Router, F
from aiogram.types import Message, CallbackQuery, InlineKeyboardButton
from aiogram.filters import Command
from aiogram.utils.keyboard import InlineKeyboardBuilder

from src.bot.keyboards import (
    get_level_keyboard,
    get_persona_keyboard,
    get_translate_keyboard,
    get_original_keyboard,
)
from src.personas import get_all_personas
from src.services.supabase_db import db
from src.services.groq_client import groq_client

router = Router()
logger = logging.getLogger(__name__)

# Кеш оригиналов для How to use (отдельный, не путаем с message.py)
_how_to_originals: Dict[int, str] = {}

HOW_TO_TEXT = """🗣 <b>How to use Speech Flow AI</b>

<b>Normal Mode</b>
1. <b>Speak or write in English</b> — your message is analyzed for errors
2. <b>Natural conversation</b> — the bot keeps the dialogue flowing
3. <b>Integrated corrections</b> — mistakes are corrected with explanation
4. <b>Vocabulary building</b> — new words are saved to your personal dictionary automatically
5. <b>Voice messages recommended</b> — speaking practice is the fastest path to fluency

<b>▶ Flow Mode</b>
6. <b>Press ▶ Flow</b> to enter Flow Mode — no corrections, no analysis, just real conversation
7. <b>Choose your conversation partner</b> — six different people, each with their own personality and voice
8. <b>Talk freely</b> — your partner listens and responds in their own voice, just like a real person
9. <b>Switch partners anytime</b> — tap the Switch button during a conversation to talk to someone new. Your new partner will naturally pick up where the conversation left off.
10. <b>Press ⏹ Stop Flow</b> to return to Normal Mode

💡 <b>Tips</b>
• Don't worry about mistakes — that's how we learn
• Flow Mode is where real fluency happens — use it often
• Try different conversation partners to keep things fresh
• Check your stats regularly to track progress"""


# ─── Команды ───────────────────────────────────────────────────────────────

@router.message(Command("stats"))
async def cmd_stats(message: Message):
    try:
        stats = await db.get_user_stats(message.from_user.id)
        user = stats.get("user", {})

        level = str(user.get('level', 'Not set')).upper()
        streak = user.get('streak_days', 0)
        msgs = user.get('free_messages_used', 0)
        vocab_count = stats.get('vocabulary_count', 0)
        persona = user.get('persona', 'greg').capitalize()

        stats_text = (
            f"📊 <b>Your Speech Flow Stats</b>\n\n"
            f"👤 <b>Profile:</b>\n"
            f"• Level: <b>{level}</b>\n"
            f"• Conversation partner: <b>{persona}</b>\n"
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
    """Смена собеседника — показываем выбор персонажей"""
    personas = get_all_personas()
    builder = InlineKeyboardBuilder()
    for key, name in personas.items():
        builder.row(InlineKeyboardButton(text=name, callback_data=f"persona_{key}"))

    await message.answer(
        "🗣 <b>Choose your conversation partner:</b>",
        reply_markup=builder.as_markup(),
        parse_mode="HTML"
    )


@router.message(Command("author"))
async def cmd_author(message: Message):
    await message.answer(
        "👨‍💻 <b>SpeechFlow AI</b>\n\n"
        "Created by: @inikonoff\n"
        "Feedback and suggestions are welcome!",
        parse_mode="HTML"
    )


# ─── Callbacks ─────────────────────────────────────────────────────────────

@router.callback_query(F.data == "how_to_use")
async def show_how_to_use(callback: CallbackQuery):
    try:
        sent = await callback.message.edit_text(
            HOW_TO_TEXT,
            parse_mode="HTML"
        )
        # Добавляем кнопку Translate
        _how_to_originals[sent.message_id] = HOW_TO_TEXT
        await sent.edit_reply_markup(
            reply_markup=get_translate_keyboard(sent.message_id)
        )
        await callback.answer()
    except Exception as e:
        logger.error(f"Error showing how to use: {e}")
        await callback.answer("Error.", show_alert=True)


@router.callback_query(F.data == "change_level")
async def change_user_level(callback: CallbackQuery):
    await callback.message.edit_text(
        "<b>Select your new English level:</b>",
        reply_markup=get_level_keyboard(),
        parse_mode="HTML"
    )
    await callback.answer()


@router.callback_query(F.data == "my_stats")
async def cq_show_stats(callback: CallbackQuery):
    try:
        stats = await db.get_user_stats(callback.from_user.id)
        user = stats.get("user", {})

        level = str(user.get('level', 'Not set')).upper()
        streak = user.get('streak_days', 0)
        vocab_count = stats.get('vocabulary_count', 0)
        persona = user.get('persona', 'greg').capitalize()

        stats_text = (
            f"📊 <b>Your Speech Flow Stats</b>\n\n"
            f"👤 <b>Profile:</b>\n"
            f"• Level: <b>{level}</b>\n"
            f"• Conversation partner: <b>{persona}</b>\n"
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

        await callback.message.edit_text(stats_text, parse_mode="HTML")
        await callback.answer()
    except Exception as e:
        logger.error(f"Error in cq_show_stats: {e}")
        await callback.answer("Error loading stats.", show_alert=True)


@router.callback_query(F.data == "my_vocabulary")
async def cq_show_vocab(callback: CallbackQuery):
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


# ─── Translate / Original для How to use ──────────────────────────────────

@router.callback_query(F.data.startswith("howto_translate_"))
async def howto_translate(callback: CallbackQuery):
    try:
        message_id = int(callback.data.split("_")[2])
        original = _how_to_originals.get(message_id, HOW_TO_TEXT)

        # Убираем HTML теги для перевода
        clean_text = re.sub(r'<[^>]+>', '', original)
        translation = await groq_client.translate_text(clean_text)
        safe_translation = html.escape(translation)

        await callback.message.edit_text(
            f"🌐 {safe_translation}",
            parse_mode="HTML",
            reply_markup=get_original_keyboard(message_id)
        )
        await callback.answer()
    except Exception as e:
        logger.error(f"Error translating how to use: {e}")
        await callback.answer("Translation failed.", show_alert=True)


@router.callback_query(F.data.startswith("howto_original_"))
async def howto_original(callback: CallbackQuery):
    try:
        message_id = int(callback.data.split("_")[2])
        await callback.message.edit_text(
            HOW_TO_TEXT,
            parse_mode="HTML",
            reply_markup=get_translate_keyboard(message_id)
        )
        await callback.answer()
    except Exception as e:
        logger.error(f"Error restoring how to use: {e}")
        await callback.answer("Could not restore original.", show_alert=True)
