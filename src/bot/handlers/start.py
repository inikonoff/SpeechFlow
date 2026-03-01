import logging
from aiogram import Router, F
from aiogram.filters import Command
from aiogram.types import Message, CallbackQuery, BufferedInputFile

from src.bot.keyboards import (
    get_level_keyboard,
    get_persona_keyboard,
    get_flow_start_keyboard,
    get_translate_keyboard,
)
from src.services.supabase_db import db
from src.services.groq_client import groq_client
from src.personas import get_persona_voice, get_all_personas

router = Router()
logger = logging.getLogger(__name__)


@router.message(Command("start"))
async def cmd_start(message: Message):
    try:
        await db.get_or_create_user(
            telegram_id=message.from_user.id,
            username=message.from_user.username
        )

        # Показываем Flow-кнопку сразу — она постоянная
        await message.answer(".", reply_markup=get_flow_start_keyboard())

        welcome_text = (
            "👋 Welcome to <b>Speech Flow AI</b>!\n\n"
            "I'm your AI English tutor focused on <b>conversational fluency</b>.\n\n"
            "To get started, please select your English level:"
        )

        await message.answer(
            welcome_text,
            parse_mode="HTML",
            reply_markup=get_level_keyboard()
        )

    except Exception as e:
        logger.error(f"Error in /start: {e}")
        await message.answer("An error occurred. Please try again.")


@router.callback_query(F.data.startswith("level_"))
async def process_level_and_ask_persona(callback: CallbackQuery):
    """После выбора уровня — сохраняем и предлагаем выбрать собеседника"""
    try:
        level = callback.data.split("_")[1]
        await db.update_user_level(callback.from_user.id, level)

        features_text = (
            f"✅ Level set to <b>{level.upper()}</b>.\n\n"
            "Now, <b>who would you like to talk to?</b>\n\n"
            "Each person has their own story, personality, and way of talking. "
            "You can switch anytime."
        )

        await callback.message.edit_text(
            features_text,
            parse_mode="HTML",
            reply_markup=get_persona_keyboard()
        )
        await callback.answer()

    except Exception as e:
        logger.error(f"Error in level selection: {e}")
        await callback.answer("An error occurred.", show_alert=True)


@router.callback_query(F.data.startswith("persona_"))
async def process_persona_selection(callback: CallbackQuery):
    """Выбор персонажа — сохраняем, персонаж приветствует голосом"""
    try:
        persona_key = callback.data.split("_", 1)[1]
        user_id = callback.from_user.id

        # Сохраняем персонажа и голос пользователю
        voice = get_persona_voice(persona_key)
        await db.update_user_persona(user_id, persona_key)
        await db.update_user_voice(user_id, voice)

        # Получаем уровень
        user = await db.get_or_create_user(user_id)
        user_level = user.get("level", "intermediate")

        await callback.message.edit_text(
            "One moment...",
            parse_mode="HTML"
        )

        # Генерируем приветствие персонажа
        greeting = await groq_client.generate_persona_greeting(persona_key, user_level)

        # Сохраняем приветствие в историю
        await db.save_message(user_id, "assistant", greeting)

        # Отправляем голосовое приветствие
        voice_bytes = await groq_client.text_to_speech(greeting, voice=voice)

        if voice_bytes:
            voice_file = BufferedInputFile(voice_bytes, filename="greeting.wav")
            await callback.message.answer_voice(voice_file)

        # Текст с кнопкой Translate
        from aiogram.utils import html as aiogram_html
        safe_greeting = aiogram_html.escape(greeting)
        sent = await callback.message.answer(
            f"💬 {safe_greeting}",
            parse_mode="HTML"
        )
        await db.save_message(user_id, "assistant", greeting)
        await sent.edit_reply_markup(reply_markup=get_translate_keyboard(sent.message_id))

        # Сохраняем оригинал для кнопки Original
        from src.bot.handlers.message import _originals_cache
        _originals_cache[sent.message_id] = greeting

        await callback.answer()

    except Exception as e:
        logger.error(f"Error in persona selection: {e}")
        await callback.answer("An error occurred.", show_alert=True)
