import logging
import re
from io import BytesIO
from aiogram import Router, types
from aiogram.types import Message, BufferedInputFile
from aiogram.filters import Command

from src.config import settings, ADMIN_IDS
from src.services.supabase_db import db
from src.services.groq_client import groq_client
from src.utils.audio import save_voice_file, cleanup_file, read_file_bytes

router = Router()
logger = logging.getLogger(__name__)


async def transcribe_voice_with_groq(voice_file_bytes: bytes) -> str:
    """Транскрибация голоса через Groq Whisper API"""
    try:
        # Сохраняем временный файл
        ogg_path = await save_voice_file(voice_file_bytes, "ogg")
        
        try:
            # Читаем файл для отправки в Groq
            audio_bytes = await read_file_bytes(ogg_path)
            
            # Отправляем в Groq
            text = await groq_client.transcribe_audio(audio_bytes)
            return text
            
        finally:
            # Очищаем временный файл
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
async def handle_message(message: Message):
    """Основной обработчик текстовых и голосовых сообщений"""
    try:
        user_id = message.from_user.id
        is_voice_input = False
        
        # Проверяем лимиты (если не админ)
        is_admin = user_id in ADMIN_IDS
        if not is_admin and settings.FREE_MESSAGES_LIMIT > 0:
            user = await db.get_or_create_user(user_id)
            if user.get("free_messages_used", 0) >= settings.FREE_MESSAGES_LIMIT:
                await message.answer(
                    "You've reached your message limit. Please upgrade to continue.",
                    parse_mode="Markdown"
                )
                return
        
        # Определяем текст сообщения
        if message.voice:
            # Голосовое сообщение
            is_voice_input = True
            await message.bot.send_chat_action(user_id, "typing")
            
            # Скачиваем файл
            voice_file = await message.bot.get_file(message.voice.file_id)
            voice_bytes = await message.bot.download_file(voice_file.file_path)
            
            # Транскрибируем через Groq
            user_text = await transcribe_voice_with_groq(voice_bytes.read())
            
            if not user_text or user_text.startswith("[Transcription error"):
                await message.answer("Could not transcribe your voice message. Please try again.")
                return
                
            # Отправляем транскрипцию пользователю
            await message.answer(f"🎤 *You said:* {user_text}", parse_mode="Markdown")
            
        elif message.text:
            # Текстовое сообщение
            user_text = message.text.strip()
            
            if not user_text or user_text.startswith("/"):
                return
        else:
            return
        
        # Получаем данные пользователя
        user = await db.get_or_create_user(user_id)
        user_level = user.get("level", settings.DEFAULT_USER_LEVEL)
        
        # Определяем, нужно ли отвечать голосом
        should_reply_voice = (
            settings.VOICE_RESPONSE_MODE == "always" or 
            (settings.VOICE_RESPONSE_MODE == "mirror" and is_voice_input)
        )
        
        # Отладочный лог
        logger.info(f"Voice response mode: {settings.VOICE_RESPONSE_MODE}, is_voice_input: {is_voice_input}, should_reply_voice: {should_reply_voice}")
        
        # Показываем индикатор (запись голоса или набор текста)
        if should_reply_voice:
            await message.bot.send_chat_action(user_id, "record_voice")
        else:
            await message.bot.send_chat_action(user_id, "typing")
        
        # Обрабатываем сообщение через Speech Flow AI
        response, analysis_data = await groq_client.process_user_message(
            telegram_id=user_id,
            user_text=user_text,
            user_level=user_level
        )
        
        # Отправляем ответ в стиле Engify:
        # 1. Сначала анализ текстом (коррекция + объяснение)
        # 2. Потом диалог голосом (если включено)
        
        if should_reply_voice:
            # Формируем анализ (только коррекция)
            analysis_text = f"""✅ **Correct**
{analysis_data.get('corrected_sentence', user_text)}

💡 **Why**
{analysis_data.get('explanation', 'No corrections needed.')}"""
            
            if analysis_data.get('vocabulary_items'):
                analysis_text += "\n\n📚 *New words added to your vocabulary*"
            
            # 1. Отправляем анализ текстом
            await message.answer(analysis_text, parse_mode="Markdown")
            
            # 2. Генерируем голосовой ответ (только диалог)
            logger.info("🎤 Generating voice response...")
            chat_response = analysis_data.get('chat_response', response)
            logger.info(f"💬 Chat response: {chat_response[:200]}...")
            
            # Слегка сокращаем для TTS, но сохраняем живость
            tts_text = chat_response
            
            # Если ответ длинный (больше 3 предложений), берём первые 2-3 предложения
            sentences = re.split(r'(?<=[.!?])\s+', chat_response)
            if len(sentences) > 3:
                # Берём первые 2-3 предложения так, чтобы сохранить вопрос
                tts_text = ' '.join(sentences[:2])
                # Если в первых двух нет вопроса, добавляем третье
                if '?' not in tts_text and len(sentences) > 2:
                    tts_text = ' '.join(sentences[:3])
                logger.info(f"📏 TTS text slightly trimmed: {len(sentences)} → {len(tts_text.split('.'))} sentences")
            
            logger.info(f"🎤 Sending to TTS: {tts_text[:100]}...")
            voice_bytes = await groq_client.text_to_speech(tts_text)
            
            if voice_bytes:
                logger.info(f"✅ Voice generated: {len(voice_bytes)} bytes")
                
                # Проверяем, что это не пустой файл
                if len(voice_bytes) < 100:
                    logger.error(f"❌ Voice too small: {len(voice_bytes)} bytes")
                    # Отправляем только текст
                    await message.answer(f"💬 {chat_response}", parse_mode="Markdown")
                    return
                
                # Пробуем отправить как голос
                try:
                    voice_file = BufferedInputFile(voice_bytes, filename="response.ogg")
                    await message.answer_voice(voice_file)
                    logger.info("✅ Voice message sent successfully")
                    
                    # Дублируем полный диалог текстом для удобства
                    await message.answer(f"💬 {chat_response}", parse_mode="Markdown")
                except Exception as e:
                    logger.error(f"❌ Failed to send voice: {e}", exc_info=True)
                    # Fallback на текст
                    await message.answer(f"💬 {chat_response}", parse_mode="Markdown")
            else:
                logger.warning("⚠️ No voice generated, sending text only")
                await message.answer(f"💬 {chat_response}", parse_mode="Markdown")
        else:
            # Текстовый режим: весь ответ текстом
            logger.info("📝 Sending text-only response")
            await message.answer(response, parse_mode="Markdown")
        
    except Exception as e:
        logger.error(f"❌ Error handling message: {e}", exc_info=True)
        await message.answer(
            "Sorry, I encountered an error processing your message. Please try again.",
            parse_mode="Markdown"
        )