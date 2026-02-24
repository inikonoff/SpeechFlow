import logging
from io import BytesIO
from aiogram import Router, types
from aiogram.types import Message, BufferedInputFile
from aiogram.filters import Command
from typing import Dict, Any

from src.config import settings, ADMIN_IDS
from src.services.supabase_db import db
from src.services.groq_client import groq_client
from src.utils.audio import save_voice_file, cleanup_file, read_file_bytes

router = Router()
logger = logging.getLogger(__name__)


async def transcribe_voice_with_groq(voice_file_bytes: bytes) -> str:
    """Транскрибация голоса через Groq Whisper API"""
    try:
        ogg_path = await save_voice_file(voice_file_bytes, "ogg")
        
        try:
            audio_bytes = await read_file_bytes(ogg_path)
            text = await groq_client.transcribe_audio(audio_bytes)
            return text
        finally:
            await cleanup_file(ogg_path)
                
    except Exception as e:
        logger.error(f"Error transcribing voice with Groq: {e}")
        raise


@router.message(Command("stats"))
async def cmd_stats(message: Message):
    """Команда /stats"""
    from src.bot.handlers.menu import show_user_stats
    
    class FakeCallback:
        def __init__(self, user_id, message):
            self.from_user = types.User(id=user_id, is_bot=False, first_name="")
            self.message = message
            self.data = "my_stats"
    
    fake_callback = FakeCallback(message.from_user.id, message)
    await show_user_stats(fake_callback)


@router.message()
async def handle_message(message: Message, user: Dict[str, Any] = None, is_admin: bool = False):
    """Основной обработчик текстовых и голосовых сообщений.
    
    user и is_admin приходят из UserMiddleware — повторный вызов к БД не нужен.
    """
    try:
        user_id = message.from_user.id
        is_voice_input = False

        # Если middleware не отработал (например, в тестах) — фолбэк
        if user is None:
            user = await db.get_or_create_user(user_id)
            is_admin = user_id in ADMIN_IDS

        # Проверяем лимиты (если не админ)
        if not is_admin and settings.FREE_MESSAGES_LIMIT > 0:
            if user.get("free_messages_used", 0) >= settings.FREE_MESSAGES_LIMIT:
                await message.answer(
                    "You've reached your message limit. Please upgrade to continue.",
                    parse_mode="Markdown"
                )
                return
        
        # Определяем текст сообщения
        if message.voice:
            is_voice_input = True
            await message.bot.send_chat_action(user_id, "typing")
            
            voice_file = await message.bot.get_file(message.voice.file_id)
            voice_bytes = await message.bot.download_file(voice_file.file_path)
            
            user_text = await transcribe_voice_with_groq(voice_bytes.read())
            
            if not user_text or user_text.startswith("[Transcription error"):
                await message.answer("Could not transcribe your voice message. Please try again.")
                return
                
            await message.answer(f"🎤 *You said:* {user_text}", parse_mode="Markdown")
            
        elif message.text:
            user_text = message.text.strip()
            
            if not user_text or user_text.startswith("/"):
                return
        else:
            return
        
        user_level = user.get("level", settings.DEFAULT_USER_LEVEL)
        
        should_reply_voice = (
            settings.VOICE_RESPONSE_MODE == "always" or 
            (settings.VOICE_RESPONSE_MODE == "mirror" and is_voice_input)
        )
        
        logger.info(f"Voice response mode: {settings.VOICE_RESPONSE_MODE}, is_voice_input: {is_voice_input}, should_reply_voice: {should_reply_voice}")
        
        if should_reply_voice:
            await message.bot.send_chat_action(user_id, "record_voice")
        else:
            await message.bot.send_chat_action(user_id, "typing")
        
        response, analysis_data = await groq_client.process_user_message(
            telegram_id=user_id,
            user_text=user_text,
            user_level=user_level
        )
        
        if should_reply_voice:
            analysis_text = f"""✅ **Correct**
{analysis_data.get('corrected_sentence', user_text)}

💡 **Why**
{analysis_data.get('explanation', 'No corrections needed.')}"""
            
            if analysis_data.get('vocabulary_items'):
                analysis_text += "\n\n📚 *New words added to your vocabulary*"
            
            await message.answer(analysis_text, parse_mode="Markdown")
            
            logger.info("Generating voice response...")
            chat_response = analysis_data.get('chat_response', response)
            voice_bytes = await groq_client.text_to_speech(chat_response)
            
            if voice_bytes:
                logger.info(f"Voice generated successfully: {len(voice_bytes)} bytes")
                voice_file = BufferedInputFile(voice_bytes, filename="response.wav")
                await message.answer_voice(voice_file)
                logger.info("Voice message sent")
                await message.answer(f"💬 {chat_response}", parse_mode="Markdown")
            else:
                logger.warning("TTS failed, sending chat response as text")
                await message.answer(f"💬 {chat_response}", parse_mode="Markdown")
        else:
            logger.info("Sending text-only response")
            await message.answer(response, parse_mode="Markdown")
        
    except Exception as e:
        logger.error(f"Error handling message: {e}")
        await message.answer(
            "Sorry, I encountered an error processing your message. Please try again.",
            parse_mode="Markdown"
        )
