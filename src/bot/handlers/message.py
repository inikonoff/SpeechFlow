import logging
import html
import asyncio
from aiogram import Router, F
from aiogram.types import Message, BufferedInputFile, CallbackQuery
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from typing import Dict, Any

from src.config import settings, ADMIN_IDS
from src.services.supabase_db import db
from src.services.groq_client import groq_client
from src.utils.audio import save_voice_file, cleanup_file, read_file_bytes
from src.personas import get_persona_voice, get_all_personas
from src.bot.keyboards import (
    get_translate_keyboard,
    get_original_keyboard,
    get_flow_start_keyboard,
    get_flow_stop_keyboard,
    get_flow_active_keyboard,
    get_persona_keyboard,
)

router = Router()
logger = logging.getLogger(__name__)

# Кеш оригинальных текстов для кнопки Original: {message_id: original_text}
_originals_cache: Dict[int, str] = {}

# Порог схлопывания саммари
SUMMARY_MERGE_THRESHOLD = 4


# ─── FSM ───────────────────────────────────────────────────────────────────

class FlowState(StatesGroup):
    choosing_persona = State()
    active = State()


# ─── Helpers ───────────────────────────────────────────────────────────────

async def transcribe_voice_with_groq(voice_file_bytes: bytes) -> str:
    try:
        ogg_path = await save_voice_file(voice_file_bytes, "ogg")
        try:
            audio_bytes = await read_file_bytes(ogg_path)
            return await groq_client.transcribe_audio(audio_bytes)
        finally:
            await cleanup_file(ogg_path)
    except Exception as e:
        logger.error(f"Error transcribing voice: {e}")
        raise


async def send_response_with_translate(
    message: Message,
    chat_response: str,
    should_reply_voice: bool,
    voice: str,
    analysis_text: str = None,
    extra_keyboard=None
):
    """
    Отправляет ответ с кнопкой Translate.
    extra_keyboard — дополнительные кнопки (Switch в Flow Mode).
    """
    safe_response = html.escape(chat_response)

    if should_reply_voice:
        voice_bytes = await groq_client.text_to_speech(chat_response, voice=voice)
        if voice_bytes:
            voice_file = BufferedInputFile(voice_bytes, filename="response.wav")
            await message.answer_voice(voice_file)

    text_body = f"💬 {safe_response}"
    if analysis_text and not should_reply_voice:
        text_body = f"{text_body}\n\n{analysis_text}"

    sent = await message.answer(text_body, parse_mode="HTML")
    _originals_cache[sent.message_id] = chat_response

    if extra_keyboard:
        from aiogram.utils.keyboard import InlineKeyboardBuilder
        from aiogram.types import InlineKeyboardButton
        builder = InlineKeyboardBuilder()
        builder.row(
            InlineKeyboardButton(text="🌐 Translate", callback_data=f"translate_{sent.message_id}")
        )
        for row in extra_keyboard.inline_keyboard:
            builder.row(*row)
        await sent.edit_reply_markup(reply_markup=builder.as_markup())
    else:
        await sent.edit_reply_markup(reply_markup=get_translate_keyboard(sent.message_id))


async def run_summarization(user_id: int) -> None:
    """
    Фоновая задача: саммаризация диалога после прощания.
    1. Берём последние сообщения
    2. Генерируем саммари с учётом существующего контекста
    3. Сохраняем в БД
    4. Если саммари >= SUMMARY_MERGE_THRESHOLD — схлопываем
    """
    try:
        messages = await db.get_messages_for_summary(user_id, limit=30)
        if not messages:
            return

        existing_summary = await db.get_latest_summary(user_id)
        new_summary = await groq_client.summarize_conversation(messages, existing_summary)

        if not new_summary:
            return

        await db.save_summary(user_id, new_summary, is_merged=False)
        logger.info(f"✅ Summary saved for user {user_id}")

        # Проверяем нужно ли схлопывать
        unmerged_count = await db.count_unmerged_summaries(user_id)
        if unmerged_count >= SUMMARY_MERGE_THRESHOLD:
            await run_merge_summaries(user_id)

    except Exception as e:
        logger.error(f"Error in summarization for user {user_id}: {e}")


async def run_merge_summaries(user_id: int) -> None:
    """Схлопывает накопившиеся саммари в один"""
    try:
        unmerged = await db.get_unmerged_summaries(user_id)
        if len(unmerged) < SUMMARY_MERGE_THRESHOLD:
            return

        contents = [s["content"] for s in unmerged]
        merged_content = await groq_client.merge_summaries(contents)

        if not merged_content:
            return

        # Сохраняем схлопнутый саммари
        await db.save_summary(user_id, merged_content, is_merged=True)

        # Помечаем старые как вошедшие в схлопывание
        ids = [s["id"] for s in unmerged]
        await db.mark_summaries_as_merged(user_id, ids)

        logger.info(f"✅ Summaries merged for user {user_id}: {len(unmerged)} → 1")

    except Exception as e:
        logger.error(f"Error merging summaries for user {user_id}: {e}")


# ─── Flow Mode: активация ──────────────────────────────────────────────────

@router.message(F.text == "▶ Flow")
async def activate_flow(message: Message, state: FSMContext):
    await state.set_state(FlowState.choosing_persona)
    await message.answer(
        "Who would you like to talk to?",
        reply_markup=get_persona_keyboard()
    )


@router.message(F.text == "⏹ Stop Flow")
async def deactivate_flow(message: Message, state: FSMContext):
    await state.clear()
    await message.answer(
        "Flow Mode off. Back to normal mode.",
        reply_markup=get_flow_start_keyboard()
    )


# ─── Flow Mode: выбор персонажа ────────────────────────────────────────────

@router.callback_query(F.data.startswith("persona_"), FlowState.choosing_persona)
async def flow_persona_selected(callback: CallbackQuery, state: FSMContext):
    try:
        persona_key = callback.data.split("_", 1)[1]
        user_id = callback.from_user.id
        voice = get_persona_voice(persona_key)

        fsm_data = await state.get_data()
        switch_context = fsm_data.get("switch_context", "")

        await state.update_data(persona_key=persona_key, voice=voice, switch_context="")
        await state.set_state(FlowState.active)
        await db.update_user_persona(user_id, persona_key)
        await db.update_user_voice(user_id, voice)

        user = await db.get_or_create_user(user_id)
        user_level = user.get("level", "intermediate")

        await callback.message.edit_text("...")

        # Новый или Switch — разные приветствия
        if switch_context:
            greeting = await groq_client.generate_switch_opener(persona_key, switch_context)
        else:
            greeting = await groq_client.generate_persona_greeting(persona_key, user_level)

        await db.save_message(user_id, "assistant", greeting)

        personas = get_all_personas()
        persona_name = personas.get(persona_key, persona_key.capitalize())
        switch_kb = get_flow_active_keyboard(persona_name)

        await send_response_with_translate(
            callback.message,
            greeting,
            should_reply_voice=True,
            voice=voice,
            extra_keyboard=switch_kb
        )
        await callback.answer()

    except Exception as e:
        logger.error(f"Error in flow persona selection: {e}")
        await callback.answer("Something went wrong.", show_alert=True)


# ─── Flow Mode: Switch ─────────────────────────────────────────────────────

@router.callback_query(F.data == "flow_switch")
async def flow_switch(callback: CallbackQuery, state: FSMContext):
    try:
        user_id = callback.from_user.id

        # Сохраняем контекст текущего диалога для нового персонажа
        history = await db.get_history(user_id, limit=settings.CONTEXT_WINDOW)
        context_text = " | ".join([
            f"{m['role']}: {m['content']}" for m in history
        ]) if history else ""

        await state.update_data(switch_context=context_text)
        await state.set_state(FlowState.choosing_persona)

        await callback.message.answer(
            "Who would you like to talk to next?",
            reply_markup=get_persona_keyboard()
        )
        await callback.answer()

    except Exception as e:
        logger.error(f"Error in flow switch: {e}")
        await callback.answer("Something went wrong.", show_alert=True)


# ─── Flow Mode: обработка сообщений ───────────────────────────────────────

@router.message(FlowState.active)
async def handle_flow_message(message: Message, state: FSMContext, user: Dict[str, Any] = None):
    try:
        user_id = message.from_user.id

        if user is None:
            user = await db.get_or_create_user(user_id)

        fsm_data = await state.get_data()
        persona_key = fsm_data.get("persona_key") or user.get("persona", "greg")
        voice = fsm_data.get("voice") or get_persona_voice(persona_key)

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

        # Детектируем прощание параллельно с получением истории
        farewell_task = asyncio.create_task(groq_client.detect_farewell(user_text))
        history_task = asyncio.create_task(db.get_history(user_id, limit=settings.CONTEXT_WINDOW))
        summary_task = asyncio.create_task(db.get_latest_summary(user_id))

        history, summary, is_farewell = await asyncio.gather(
            history_task, summary_task, farewell_task
        )

        await message.bot.send_chat_action(user_id, "record_voice")
        chat_response = await groq_client.generate_flow_response(
            text=user_text,
            persona_key=persona_key,
            history=history,
            summary=summary
        )
        await db.save_message(user_id, "assistant", chat_response)

        personas = get_all_personas()
        persona_name = personas.get(persona_key, persona_key.capitalize())
        switch_kb = get_flow_active_keyboard(persona_name)

        await send_response_with_translate(
            message,
            chat_response,
            should_reply_voice=True,
            voice=voice,
            extra_keyboard=switch_kb
        )

        # Если прощание — запускаем саммаризацию в фоне
        if is_farewell:
            asyncio.create_task(run_summarization(user_id))
            logger.info(f"Farewell detected for user {user_id}, summarization scheduled")

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

        await db.save_message(user_id, "user", user_text)

        # Детектируем прощание параллельно с остальным
        farewell_task = asyncio.create_task(groq_client.detect_farewell(user_text))
        history_task = asyncio.create_task(db.get_history(user_id, limit=settings.CONTEXT_WINDOW))

        history, is_farewell = await asyncio.gather(history_task, farewell_task)

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

        voice = user.get("voice") or settings.TTS_VOICE

        if should_reply_voice:
            await message.answer(analysis_text, parse_mode="HTML")

        await send_response_with_translate(
            message,
            chat_response,
            should_reply_voice=should_reply_voice,
            voice=voice,
            analysis_text=analysis_text if not should_reply_voice else None
        )

        # Прощание — саммаризация в фоне
        if is_farewell:
            asyncio.create_task(run_summarization(user_id))
            logger.info(f"Farewell detected for user {user_id}, summarization scheduled")

    except Exception as e:
        logger.error(f"Error handling message: {e}")
        await message.answer("Sorry, I encountered an error processing your message.", parse_mode="HTML")


# ─── Translate / Original ──────────────────────────────────────────────────

@router.callback_query(F.data.startswith("translate_"))
async def handle_translate(callback: CallbackQuery):
    try:
        message_id = int(callback.data.split("_")[1])
        original_text = _originals_cache.get(message_id)

        if not original_text:
            raw = callback.message.text or ""
            original_text = raw.removeprefix("💬 ").strip()

        translation = await groq_client.translate_text(original_text)
        safe_translation = html.escape(translation)

        # Сохраняем остальные кнопки (Switch если был)
        current_markup = callback.message.reply_markup
        from aiogram.utils.keyboard import InlineKeyboardBuilder
        from aiogram.types import InlineKeyboardButton
        builder = InlineKeyboardBuilder()
        builder.row(
            InlineKeyboardButton(text="🔤 Original", callback_data=f"original_{message_id}")
        )
        if current_markup:
            for row in current_markup.inline_keyboard:
                for btn in row:
                    if "translate_" not in btn.callback_data and "original_" not in btn.callback_data:
                        builder.row(btn)

        await callback.message.edit_text(
            f"🌐 {safe_translation}",
            parse_mode="HTML",
            reply_markup=builder.as_markup()
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

        current_markup = callback.message.reply_markup
        from aiogram.utils.keyboard import InlineKeyboardBuilder
        from aiogram.types import InlineKeyboardButton
        builder = InlineKeyboardBuilder()
        builder.row(
            InlineKeyboardButton(text="🌐 Translate", callback_data=f"translate_{message_id}")
        )
        if current_markup:
            for row in current_markup.inline_keyboard:
                for btn in row:
                    if "translate_" not in btn.callback_data and "original_" not in btn.callback_data:
                        builder.row(btn)

        await callback.message.edit_text(
            f"💬 {safe_original}",
            parse_mode="HTML",
            reply_markup=builder.as_markup()
        )
        await callback.answer()
    except Exception as e:
        logger.error(f"Error in original callback: {e}")
        await callback.answer("Could not restore original.", show_alert=True)
