import logging
import whisper
from aiogram import Router, types
from aiogram.types import Message
from aiogram.filters import Command

from src.config import settings
from src.services.supabase_db import db
from src.services.groq_client import groq_client
from src.utils.audio import save_voice_file, convert_ogg_to_mp3, cleanup_file

router = Router()
logger = logging.getLogger(__name__)

# Загружаем модель Whisper (ленивая загрузка)
_whisper_model = None

def get_whisper_model():
    """Ленивая загрузка модели Whisper"""
    global _whisper_model
    if _whisper_model is None:
        _whisper_model = whisper.load_model("base")
    return _whisper_model


async def transcribe_voice_message(voice_file_bytes: bytes) -> str:
    """Транскрибируем голосовое сообщение через Whisper"""
    try:
        # Сохраняем временный файл
        ogg_path = await save_voice_file(voice_file_bytes, "ogg")
        mp3_path = None
        
        try:
            # Конвертируем в MP3
            mp3_path = await convert_ogg_to_mp3(ogg_path)
            
            # Транскрибируем
            model = get_whisper_model()
            result = model.transcribe(str(mp3_path))
            
            return result["text"].strip()
            
        finally:
            # Очищаем временные файлы
            await cleanup_file(ogg_path)
            if mp3_path:
                await cleanup_file(mp3_path)
                
    except Exception as e:
        logger.error(f"Error transcribing voice: {e}")
        raise


@router.message(Command("stats"))
async def cmd_stats(message: Message):
    """Команда /stats - альтернатива кнопке"""
    from src.bot.handlers.menu import show_user_stats
    # Создаем fake callback для повторного использования логики
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
        
        # Проверяем лимиты (если не админ)
        if not await db.is_admin(user_id) and settings.FREE_MESSAGES_LIMIT > 0:
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
            await message.bot.send_chat_action(user_id, "typing")
            
            # Скачиваем файл
            voice_file = await message.bot.get_file(message.voice.file_id)
            voice_bytes = await message.bot.download_file(voice_file.file_path)
            
            # Транскрибируем
            user_text = await transcribe_voice_message(voice_bytes.read())
            
            if not user_text:
                await message.answer("Could not transcribe your voice message. Please try again.")
                return
                
            # Отправляем транскрипцию пользователю
            await message.answer(f"🎤 *You said:* {user_text}", parse_mode="Markdown")
            
        elif message.text:
            # Текстовое сообщение
            user_text = message.text.strip()
            
            if not user_text:
                await message.answer("Please send a text message.")
                return
        else:
            await message.answer("Please send a text or voice message in English.")
            return
        
        # Получаем данные пользователя
        user = await db.get_or_create_user(user_id)
        user_level = user.get("level", settings.DEFAULT_USER_LEVEL)
        
        # Показываем индикатор набора текста
        await message.bot.send_chat_action(user_id, "typing")
        
        # Обрабатываем сообщение через Speech Flow AI
        response, analysis_data = await groq_client.process_user_message(
            telegram_id=user_id,
            user_text=user_text,
            user_level=user_level
        )
        
        # Отправляем ответ
        await message.answer(response, parse_mode="Markdown")
        
    except Exception as e:
        logger.error(f"Error handling message: {e}")
        await message.answer(
            "Sorry, I encountered an error processing your message. Please try again.",
            parse_mode="Markdown"
        )
