import logging
import html
from aiogram import Router, F
from aiogram.types import Message, BufferedInputFile, CallbackQuery
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from typing import Dict, Any

from src.config import settings, ADMIN_IDS
from src.services.supabase_db import db
from src.services.groq_client import groq_client
from src.utils.audio import save_voice_file, cleanup_file, read_file_bytes
from src.bot.keyboards import (
    get_translate_keyboard,
    get_original_keyboard,
    get_flow_start_keyboard,
    get_flow_stop_keyboard,
)

router = Router()
logger = logging.getLogger(__name__)

# Кеш оригинальных текстов: {message_id: original_text}
# Живёт в памяти процесса — достаточно для toggle-кнопки в рамках сессии
_originals_cache: Dict[int, str] = {}


# ─── FSM: Flow Mode ────────────────────────────────────────────────────────

class FlowState(StatesGroup):
    active = State()


# ─── Вспомогательные функции ───────────────────────────────────────────────

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


async def send_response_with_translate(
    message: Message,
    chat_response: str,
    should_reply_voice: bool,
    user: Dict[str, Any],
    analysis_text: str = None
):
    """
    Отправляет ответ бота с кнопкой Translate.
    Сохраняет оригинальный текст в кеш для кнопки Original.
    """
    safe_chat_response = html.escape(chat_response)

    if should_reply_voice:
        user_voice = user.get("voice") or settings.TTS_VOICE
        voice_bytes_out = await groq_client.text_to_speech(chat_response, voice=user_voice)

        if voice_bytes_out:
            voice_file_out = BufferedInputFile(voice_bytes_out, filename="response.wav")
            await message.answer_voice(voice_file_out)

        # Текстовая расшифровка — сначала отправляем с placeholder, потом обновляем клавиатуру
        sent = await message.answer(f"💬 {safe_chat_response}", parse_mode="HTML")
        _originals_cache[sent.message_id] = chat_response
        await sent.edit_reply_markup(reply_markup=get_translate_keyboard(sent.message_id))

    else:
        text_body = f"💬 {safe_chat_response}"
        if analysis_text:
            text_body = f"{text_body}\n\n{analysis_text}"

        sent = await message.answer(text_body, parse_mode="HTML")
        _originals_cache[sent.message_id] = chat_response
        await sent.edit_reply_markup(reply_markup=get_translate_keyboard(sent.message_id))


# ─── Flow Mode: активация / деактивация ───────────────────────────────────

@router.message(F.text == "▶ Flow")
async def activate_flow(message: Message, state: FSMContext):
    await state.set_state(FlowState.active)
    await message.answer(
        "Flow Mode activated.\n\nSpeak English — I'll listen and respond. No corrections, no analysis. Just talk.",
        reply_markup=get_flow_stop_keyboard()
    )


@router.message(F.text == "⏹ Stop Flow")
async def deactivate_flow(message: Message, state: FSMContext):
    await state.clear()
    await message.answer(
        "Flow Mode off. Back to normal mode.",
        reply_markup=get_flow_start_keyboard()
    )


# ─── Flow Mode: обработка сообщений ───────────────────────────────────────

@router.message(FlowState.active)
async def handle_flow_message(message: Message, state: FSMContext, user: Dict[str, Any] = None):
    try:
        user_id = message.from_user.id

        if user is None:
            user = await db.get_or_create_user(user_id)

        if message.voice:
            await message.bot.send_chat_action(user_id, "typing")
            voice_file = await message.bot.get_file(message.voice.file_id)
            voice_bytes = await message.bot.download_file(voice_file.file_path)
            user_text = await transcribe_voice_with_groq(voice_bytes.read())

            if not user_text:
                await message.answer("Couldn't hear that. Try again.")
                return

        elif message.text:
            user_text = message.text.strip()
        else:
            return

        user_level = user.get("level", settings.DEFAULT_USER_LEVEL)

        await db.save_message(user_id, "user", user_text)
        history = await db.get_history(user_id, limit=settings.CONTEXT_WINDOW)

        await message.bot.send_chat_action(user_id, "record_voice")
        chat_response = await groq_client.process_flow_message(user_text, user_level, history=history)

        await db.save_message(user_id, "assistant", chat_response)

        await send_response_with_translate(
            message, chat_response, should_reply_voice=True, user=user
        )

    except Exception as e:
        logger.error(f"Error in flow message: {e}")
        await message.answer("Something went wrong. Try again.")


# ─── Обычный режим ────────────────────────────────────────────────────────

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
            voice_bytes = await message.bot.download_file(voice_file.file_path)

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

        # История
        await db.save_message(user_id, "user", user_text)
        history = await db.get_history(user_id, limit=settings.CONTEXT_WINDOW)

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
            user_level=user_level,
            history=history
        )

        # Сохраняем только разговорный ответ бота
        await db.save_message(user_id, "assistant", chat_response)

        if analysis_data.get('vocabulary_items'):
            for item in analysis_data['vocabulary_items']:
                await db.add_to_vocabulary(user_id, item)

        error_cat = analysis_data.get('error_category')
        if error_cat and error_cat.lower() != 'none':
            await db.log_error(user_id, {"category": error_cat, "mistake_text": user_text})

        await db.increment_user_metrics(user_id, tokens_used=0)

        safe_corrected = html.escape(analysis_data.get('corrected_sentence', user_text))
        safe_explanation = html.escape(analysis_data.get('explanation', 'Ошибок не найдено.'))

        analysis_text = f"✅ <b>Correct</b>\n{safe_corrected}\n\n💡 <b>Why</b>\n{safe_explanation}"

        if analysis_data.get('vocabulary_items'):
            analysis_text += "\n\n📚 <i>New words added to your vocabulary</i>"

        if not chat_response:
            chat_response = "Sorry, I couldn't formulate a response."

        if should_reply_voice:
            await message.answer(analysis_text, parse_mode="HTML")

        await send_response_with_translate(
            message,
            chat_response,
            should_reply_voice=should_reply_voice,
            user=user,
            analysis_text=analysis_text if not should_reply_voice else None
        )

    except Exception as e:
        logger.error(f"Error handling message: {e}")
        await message.answer("Sorry, I encountered an error processing your message.", parse_mode="HTML")


# ─── Translate / Original callbacks ───────────────────────────────────────

@router.callback_query(F.data.startswith("translate_"))
async def handle_translate(callback: CallbackQuery):
    try:
        message_id = int(callback.data.split("_")[1])
        original_text = _originals_cache.get(message_id)

        if not original_text:
            # Fallback: достаём из текста сообщения
            raw = callback.message.text or ""
            original_text = raw.removeprefix("💬 ").strip()

        translation = await groq_client.translate_text(original_text)
        safe_translation = html.escape(translation)

        await callback.message.edit_text(
            f"🌐 {safe_translation}",
            parse_mode="HTML",
            reply_markup=get_original_keyboard(message_id)
        )
        await callback.answer()
    except Exception as e:
        logger.error(f"Error in translate callback: {e}")
        await callback.answer("Translation failed.", show_alert=True)


@router.callback_query(F.data.startswith("original_"))
async def handle_original(callback: CallbackQuery):
    try:
        message_id = int(callback.data.split("_")[1])
        original_text = _originals_cache.get(message_id)

        if not original_text:
            await callback.answer("Original text not available.", show_alert=True)
            return

        safe_original = html.escape(original_text)

        await callback.message.edit_text(
            f"💬 {safe_original}",
            parse_mode="HTML",
            reply_markup=get_translate_keyboard(message_id)
        )
        await callback.answer()
    except Exception as e:
        logger.error(f"Error in original callback: {e}")
        await callback.answer("Could not restore original.", show_alert=True)
