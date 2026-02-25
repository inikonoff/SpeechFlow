import logging
import html
from io import BytesIO
from aiogram import Router, types
from aiogram.types import Message, BufferedInputFile
from typing import Dict, Any

from src.config import settings, ADMIN_IDS
from src.services.supabase_db import db
from src.services.groq_client import groq_client
from src.utils.audio import save_voice_file, cleanup_file, read_file_bytes

router = Router()
logger = logging.getLogger(__name__)

async def transcribe_voice_with_groq(voice_file_bytes: bytes) -> str:
    try:
        ogg_path = await save_voice_file(voice_file_bytes, "ogg")
        try:
            audio_bytes = await read_file_bytes(ogg_path)
            text = await groq_client.transcribe_audio(audio_bytes)
            return text
        finally:
            await cleanup_file(ogg_path)
    except Exception as e:
        logger.error(f"Error transcribing voice: {e}")
        raise

@router.message()
async def handle_message(message: Message, user: Dict[str, Any] = None, is_admin: bool = False):
    try:
        user_id = message.from_user.id
        is_voice_input = False

        if user is None:
            user = await db.get_or_create_user(user_id)
            is_admin = user_id in ADMIN_IDS

        if not is_admin and settings.FREE_MESSAGES_LIMIT > 0:
            if user.get("free_messages_used", 0) >= settings.FREE_MESSAGES_LIMIT:
                await message.answer("You've reached your message limit. Please upgrade.")
                return
        
        if message.voice:
            is_voice_input = True
            await message.bot.send_chat_action(user_id, "typing")
            voice_file = await message.bot.get_file(message.voice.file_id)
            user_voice = user.get("voice") or settings.TTS_VOICE
            voice_bytes = await groq_client.text_to_speech(chat_response, voice=user_voice)
            
            user_text = await transcribe_voice_with_groq(voice_bytes.read())
            
            if not user_text or user_text.startswith("[Transcription error"):
                await message.answer("Could not transcribe your voice message. Please try again.")
                return
                
            safe_text = html.escape(user_text)
            await message.answer(f"🎤 <i>You said:</i> {safe_text}", parse_mode="HTML")
            
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
        
        action = "record_voice" if should_reply_voice else "typing"
        await message.bot.send_chat_action(user_id, action)
        
        chat_response, analysis_data = await groq_client.process_user_message(
            telegram_id=user_id,
            user_text=user_text,
            user_level=user_level
        )
        
        # === СОХРАНЕНИЕ В БАЗУ ДАННЫХ ===
        if analysis_data.get('vocabulary_items'):
            for item in analysis_data['vocabulary_items']:
                await db.add_to_vocabulary(user_id, item)
                
        error_cat = analysis_data.get('error_category')
        if error_cat and error_cat.lower() != 'none':
            await db.log_error(user_id, {"category": error_cat, "mistake_text": user_text})
        
        await db.increment_user_metrics(user_id, tokens_used=0) # Обновляем streak
        # ================================

        safe_corrected = html.escape(analysis_data.get('corrected_sentence', user_text))
        safe_explanation = html.escape(analysis_data.get('explanation', 'Ошибок не найдено.'))
        
        analysis_text = f"✅ <b>Correct</b>\n{safe_corrected}\n\n💡 <b>Why</b>\n{safe_explanation}"
        
        if analysis_data.get('vocabulary_items'):
            analysis_text += "\n\n📚 <i>New words added to your vocabulary</i>"
        
        safe_chat_response = html.escape(chat_response)

        if should_reply_voice:
            await message.answer(analysis_text, parse_mode="HTML")
            voice_bytes = await groq_client.text_to_speech(chat_response)
            
            if voice_bytes:
                voice_file = BufferedInputFile(voice_bytes, filename="response.wav")
                await message.answer_voice(voice_file)
                await message.answer(f"💬 {safe_chat_response}", parse_mode="HTML")
            else:
                await message.answer(f"💬 {safe_chat_response}", parse_mode="HTML")
        else:
            # Если текстом, шлем одним сообщением
            full_text = f"💬 {safe_chat_response}\n\n{analysis_text}"
            await message.answer(full_text, parse_mode="HTML")
        
    except Exception as e:
        logger.error(f"Error handling message: {e}")
        await message.answer("Sorry, I encountered an error processing your message.", parse_mode="HTML")
