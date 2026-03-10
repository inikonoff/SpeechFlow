import logging
import html
import asyncio
import re
from aiogram import Router, F
from aiogram.types import Message, BufferedInputFile, CallbackQuery
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from src.bot.handlers.states import FlowState
from typing import Dict, Any

from src.config import settings, ADMIN_IDS
from src.services.supabase_db import db
from src.services.groq_client import groq_client
from src.utils.audio import save_voice_file, cleanup_file, read_file_bytes
from src.personas import get_persona_voice, get_persona_display, get_persona_tutor_prompt
from src.bot.keyboards import (
    get_translate_keyboard,
    get_original_keyboard,
    get_flow_start_keyboard,
    get_flow_stop_keyboard,
    get_flow_voice_keyboard,
    get_flow_voice_text_keyboard,
    get_flow_voice_translate_keyboard,
    get_flow_user_voice_keyboard,
    get_persona_keyboard,
    get_mode_keyboard,
    get_tutor_keyboard,
    get_penfriend_keyboard,
)
from src.modes import (
    MODE_TUTOR, MODE_PENFRIEND, MODE_FLOW,
    get_penfriend_system_prompt,
    CORRECTION_RATE_DEFAULT,
)

router = Router()

def _process_vocab_tags(text: str) -> tuple[str, str]:
    words = re.findall(r'\[VOCAB:([^\]]+)\]', text)
    clean_text = re.sub(r'\[VOCAB:([^\]]+)\]', r'\1', text)
    display_text = re.sub(r'\[VOCAB:([^\]]+)\]', r'<b>\1</b>', text)
    return clean_text, display_text

logger = logging.getLogger(__name__)

_originals_cache: Dict[int, str] = {}
SUMMARY_MERGE_THRESHOLD = 4

def _cache_original(msg_id: int, text: str):
    if len(_originals_cache) > 5000:
        _originals_cache.clear()
    _originals_cache[msg_id] = text

def _penfriend_typing_delay(text: str) -> float:
    words = len(text.split())
    seconds = (words / 55) * 60 * 0.7
    return max(1.4, min(seconds, 6.3))

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
    _cache_original(sent.message_id, chat_response)

    if extra_keyboard:
        from aiogram.utils.keyboard import InlineKeyboardBuilder
        from aiogram.types import InlineKeyboardButton
        builder = InlineKeyboardBuilder()
        builder.row(InlineKeyboardButton(text="🌐 Translate", callback_data=f"translate_{sent.message_id}"))
        for row in extra_keyboard.inline_keyboard:
            builder.row(*row)
        await sent.edit_reply_markup(reply_markup=builder.as_markup())
    else:
        await sent.edit_reply_markup(reply_markup=get_translate_keyboard(sent.message_id))

# Lock to prevent overlapping summarizations for the same user
_summarization_locks: Dict[int, asyncio.Lock] = {}

def _get_summary_lock(user_id: int) -> asyncio.Lock:
    if user_id not in _summarization_locks:
        _summarization_locks[user_id] = asyncio.Lock()
    return _summarization_locks[user_id]

async def run_summarization(user_id: int) -> None:
    lock = _get_summary_lock(user_id)
    if lock.locked():
        return # Skip if already summarizing
    
    async with lock:
        try:
            messages = await db.get_messages_for_summary(user_id, limit=30)
            if not messages:
                return

            existing_summary = await db.get_latest_summary(user_id)
            result = await groq_client.summarize_conversation(messages, existing_summary)
            # summarize_conversation returns (summary_text, topics_text)
            if isinstance(result, tuple):
                new_summary, new_topics = result
            else:
                new_summary, new_topics = result, ""

            if not new_summary:
                return

            await db.save_summary(user_id, new_summary, is_merged=False, topics=new_topics or None)
            logger.info(f"✅ Summary saved for user {user_id}")

            unmerged_count = await db.count_unmerged_summaries(user_id)
            if unmerged_count >= SUMMARY_MERGE_THRESHOLD:
                await run_merge_summaries(user_id)

        except Exception as e:
            logger.error(f"Error in summarization for user {user_id}: {e}")

async def run_merge_summaries(user_id: int) -> None:
    try:
        unmerged = await db.get_unmerged_summaries(user_id)
        if len(unmerged) < SUMMARY_MERGE_THRESHOLD:
            return

        contents = [s["content"] for s in unmerged]
        merged_content = await groq_client.merge_summaries(contents)

        if not merged_content:
            return

        await db.save_summary(user_id, merged_content, is_merged=True)
        ids = [s["id"] for s in unmerged]
        await db.mark_summaries_as_merged(user_id, ids)
        logger.info(f"✅ Summaries merged for user {user_id}: {len(unmerged)} → 1")

    except Exception as e:
        logger.error(f"Error merging summaries for user {user_id}: {e}")

@router.message(F.text == "🎙 Flow")
async def activate_flow(message: Message, state: FSMContext):
    await state.set_state(FlowState.choosing_persona)
    await message.answer("Who would you like to talk to?", reply_markup=get_persona_keyboard())

@router.message(F.text == "⏹ Stop Flow")
async def deactivate_flow(message: Message, state: FSMContext):
    await state.clear()
    await message.answer("⏹ Flow Mode off", reply_markup=get_mode_keyboard())

@router.message(F.text == "🎓 Tutor")
async def activate_tutor(message: Message, state: FSMContext):
    await state.clear()
    user_id = message.from_user.id
    await db.update_mode(user_id, MODE_TUTOR)
    await db.update_user_persona(user_id, "mrs_smith")
    await db.update_user_voice(user_id, get_persona_voice("mrs_smith"))
    await message.answer("🎓 Tutor Mode on — 📚 Mrs. Smith", parse_mode="HTML", reply_markup=get_tutor_keyboard())

@router.message(F.text == "⏹ Stop Tutor")
async def deactivate_tutor(message: Message, state: FSMContext):
    await state.clear()
    await message.answer("⏹ Tutor Mode off", reply_markup=get_mode_keyboard())

@router.message(F.text == "✉️ PenFriend")
async def activate_penfriend(message: Message, state: FSMContext):
    await state.set_state(FlowState.choosing_persona)
    await db.update_mode(message.from_user.id, MODE_PENFRIEND)
    await message.answer(
        "✉️ <b>PenFriend Mode</b>\n\nWho would you like to write to?",
        parse_mode="HTML",
        reply_markup=get_persona_keyboard()
    )

@router.message(F.text == "⏹ Stop PenFriend")
async def deactivate_penfriend(message: Message, state: FSMContext):
    await state.clear()
    await message.answer("⏹ PenFriend Mode off", reply_markup=get_mode_keyboard())

@router.message(F.text == "↩ Switch")
async def flow_switch_reply(message: Message, state: FSMContext):
    try:
        user_id = message.from_user.id
        history = await db.get_history(user_id, limit=settings.CONTEXT_WINDOW)
        context_text = " | ".join([
            f"{m['role']}: {m['content']}" for m in history
        ]) if history else ""

        await state.update_data(switch_context=context_text)
        await state.set_state(FlowState.choosing_persona)

        await message.answer("Who would you like to talk to next?", reply_markup=get_persona_keyboard())
    except Exception as e:
        logger.error(f"Error in flow switch reply: {e}")
        await message.answer("Something went wrong. Try again.")

@router.callback_query(F.data.startswith("persona_"), FlowState.choosing_persona)
async def flow_persona_selected(callback: CallbackQuery, state: FSMContext):
    try:
        persona_key = callback.data.split("_", 1)[1]
        user_id = callback.from_user.id
        voice = get_persona_voice(persona_key)

        fsm_data = await state.get_data()
        switch_context = fsm_data.get("switch_context", "")
        from_settings = fsm_data.get("from_settings", False)
        user = await db.get_or_create_user(user_id)

        await db.update_user_persona(user_id, persona_key)
        await db.update_user_voice(user_id, voice)

        if from_settings:
            from src.personas import get_all_personas
            display_name = get_persona_display(persona_key)
            await state.clear()
            from src.bot.keyboards import get_settings_keyboard
            user = await db.get_or_create_user(user_id)
            notif = user.get("notifications_enabled", True)
            practice = user.get("vocab_practice_enabled", False)
            await callback.message.edit_text(
                f"👤 Now talking to {display_name}.\n\n⚙️ <b>Settings</b>",
                parse_mode="HTML",
                reply_markup=get_settings_keyboard(notif, user_id, practice)
            )
            await callback.answer()
            return

        await state.update_data(
            persona_key=persona_key,
            voice=voice,
            switch_context="",
            active_mode=user.get("mode", MODE_FLOW)
        )
        await state.set_state(FlowState.active)

        user = await db.get_or_create_user(user_id)
        user_level = user.get("level", "intermediate")
        existing_summary = await db.get_latest_summary(user_id)

        await callback.message.edit_text("...")

        if switch_context:
            greeting = await groq_client.generate_switch_opener(
                persona_key, switch_context,
                session_count=user.get("session_count", 0)
            )
        else:
            greeting = await groq_client.generate_persona_greeting(
                persona_key, user_level,
                session_count=user.get("session_count", 0),
                summary=existing_summary
            )

        await db.save_message(user_id, "assistant", greeting)

        persona_display = get_persona_display(persona_key)
        if switch_context:
            await callback.message.answer(f"↩ Switched to {persona_display}", parse_mode="HTML")
        else:
            await callback.message.answer(f"🎙 Flow Mode — {persona_display}", parse_mode="HTML")

        voice_bytes = await groq_client.text_to_speech(greeting, voice=voice)
        if voice_bytes:
            voice_file = BufferedInputFile(voice_bytes, filename="greeting.wav")
            sent = await callback.message.answer_voice(
                voice_file,
                caption=persona_display,
                reply_markup=get_flow_voice_keyboard(0)
            )
            _cache_original(sent.message_id, greeting)
            await sent.edit_reply_markup(reply_markup=get_flow_voice_keyboard(sent.message_id))

        await callback.answer()

    except Exception as e:
        logger.error(f"Error in flow persona selection: {e}")
        await callback.answer("Something went wrong.", show_alert=True)

@router.message(FlowState.active)
async def handle_flow_message(message: Message, state: FSMContext, user: Dict[str, Any] = None):
    try:
        user_id = message.from_user.id

        if user is None:
            user = await db.get_or_create_user(user_id)

        fsm_data = await state.get_data()
        persona_key = fsm_data.get("persona_key") or user.get("persona", "greg")
        voice = fsm_data.get("voice") or get_persona_voice(persona_key)
        active_mode = fsm_data.get("active_mode", MODE_FLOW)

        if active_mode == MODE_PENFRIEND and message.voice:
            await message.answer(
                "✉️ PenFriend is text-only.\n"
                "Try typing what you wanted to say — it is good writing practice too."
            )
            return

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

        farewell_task = asyncio.create_task(groq_client.detect_farewell(user_text))
        history_task = asyncio.create_task(db.get_history(user_id, limit=settings.CONTEXT_WINDOW))
        summary_task = asyncio.create_task(db.get_latest_summary(user_id))
        errors_task = asyncio.create_task(db.get_top_error_categories(user_id, limit=2))

        history, summary, is_farewell, top_errors = await asyncio.gather(
            history_task, summary_task, farewell_task, errors_task
        )

        await message.bot.send_chat_action(user_id, "record_voice")
        chat_response = await groq_client.generate_flow_response(
            text=user_text,
            persona_key=persona_key,
            history=history,
            summary=summary,
            session_count=user.get("session_count", 0),
            top_errors=top_errors
        )
        await db.save_message(user_id, "assistant", chat_response)

        chat_response_clean, _ = _process_vocab_tags(chat_response)
        persona_display = get_persona_display(persona_key)

        if active_mode == MODE_PENFRIEND:
            delay = _penfriend_typing_delay(chat_response_clean)
            await message.bot.send_chat_action(user_id, "typing")
            await asyncio.sleep(delay)
            display_safe = re.sub(r'\[VOCAB:([^\]]+)\]', lambda m: f'<b>{html.escape(m.group(1))}</b>', chat_response)
            sent = await message.answer(f"💬 {display_safe}", parse_mode="HTML")
            _cache_original(sent.message_id, chat_response_clean)
            await sent.edit_reply_markup(reply_markup=get_translate_keyboard(sent.message_id))
        else:
            voice_bytes = await groq_client.text_to_speech(chat_response_clean, voice=voice)
            if voice_bytes:
                voice_file = BufferedInputFile(voice_bytes, filename="response.wav")
                sent = await message.answer_voice(
                    voice_file,
                    caption=persona_display,
                    reply_markup=get_flow_voice_keyboard(0)
                )
                _cache_original(sent.message_id, chat_response_clean)
                await sent.edit_reply_markup(reply_markup=get_flow_voice_keyboard(sent.message_id))

        if is_farewell:
            asyncio.create_task(run_summarization(user_id))
            asyncio.create_task(db.increment_session_count(user_id))
            logger.info(f"Farewell detected for user {user_id}, summarization scheduled")

    except Exception as e:
        logger.error(f"Error in flow message: {e}")
        await message.answer("Something went wrong. Try again.")

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
            await message.bot.send_chat_action(user_id, "record_voice")
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
            BUTTON_TEXTS = {"🎓 Tutor", "✉️ PenFriend", "🎙 Flow", "⏹ Stop Flow", "⏹ Stop Tutor", "⏹ Stop PenFriend", "↩ Switch"}
            if not user_text or user_text.startswith("/") or user_text in BUTTON_TEXTS:
                return
        elif message.audio or message.video_note:
            await message.answer("I only understand standard voice messages and text for now.")
            return
        else:
            return

        await db.save_message(user_id, "user", user_text)

        farewell_task = asyncio.create_task(groq_client.detect_farewell(user_text))
        history_task = asyncio.create_task(db.get_history(user_id, limit=settings.CONTEXT_WINDOW))
        summary_task = asyncio.create_task(db.get_latest_summary(user_id))
        topics_task = asyncio.create_task(db.get_topics_to_discuss(user_id))
        history, is_farewell, summary, topics = await asyncio.gather(history_task, farewell_task, summary_task, topics_task)

        user_level = user.get("level", settings.DEFAULT_USER_LEVEL)
        should_reply_voice = (
            settings.VOICE_RESPONSE_MODE == "always" or
            (settings.VOICE_RESPONSE_MODE == "mirror" and is_voice_input)
        )

        # Show "typing" while correction + response generate in parallel
        await message.bot.send_chat_action(user_id, "typing")
        chat_response, analysis_data = await groq_client.process_user_message(
            telegram_id=user_id,
            user_text=user_text,
            user_level=user_level,
            history=history,
            summary=summary,
            topics=topics
        )

        await db.save_message(user_id, "assistant", chat_response)

        if analysis_data.get('vocabulary_items'):
            for item in analysis_data['vocabulary_items']:
                await db.add_to_vocabulary(user_id, item)

        used_word = await db.find_word_in_text(user_id, user_text)
        if used_word:
            new_score = await db.increase_mastery(used_word["id"])
            if new_score >= 5:
                safe_word = html.escape(used_word["word_or_phrase"])
                await message.answer(
                    f"✅ <b>{safe_word}</b> — mastered! Added to your collection.",
                    parse_mode="HTML"
                )

        if user.get("vocab_practice_enabled", False):
            practice_words = await db.get_user_vocabulary(user_id, tab="active", limit=10)
            if practice_words:
                persona_key = user.get("persona", "mrs_smith")
                persona_prompt = get_persona_tutor_prompt(persona_key)
                practice_response = await groq_client.generate_practice_response(
                    persona_prompt=persona_prompt,
                    history=history,
                    words=practice_words,
                )
                if practice_response:
                    chat_response = practice_response

        remind_counter = await db.increment_vocab_remind_counter(user_id)
        if remind_counter >= 2:
            await db.reset_vocab_remind_counter(user_id)
            word_to_remind = await db.get_word_for_reminder(user_id)
            if word_to_remind:
                await db.mark_word_reminded(word_to_remind["id"])
                chat_response = await groq_client.generate_vocab_reminder(
                    word=word_to_remind["word_or_phrase"],
                    translation=word_to_remind.get("translation", ""),
                    bot_response=chat_response
                )

        error_cat = analysis_data.get('error_category')
        if error_cat and error_cat.lower() != 'none':
            await db.log_error(user_id, {"category": error_cat, "mistake_text": user_text})

        await db.increment_user_metrics(user_id, tokens_used=0)

        if not chat_response:
            chat_response = "Sorry, I couldn't formulate a response."

        chat_response_clean, chat_response_display = _process_vocab_tags(chat_response)
        voice = user.get("voice") or settings.TTS_VOICE
        persona_display = get_persona_display(user.get("persona", "mrs_smith"))

        if should_reply_voice:
            # Switch to record_voice indicator before TTS
            await message.bot.send_chat_action(user_id, "record_voice")
            voice_bytes_out = await groq_client.text_to_speech(chat_response_clean, voice=voice)
            if voice_bytes_out:
                voice_file_out = BufferedInputFile(voice_bytes_out, filename="response.wav")
                sent_voice = await message.answer_voice(
                    voice_file_out,
                    caption=persona_display,
                    reply_markup=get_flow_voice_keyboard(0)
                )
                _cache_original(sent_voice.message_id, chat_response_clean)
                await sent_voice.edit_reply_markup(reply_markup=get_flow_voice_keyboard(sent_voice.message_id))
        else:
            # Text response
            display_safe = re.sub(r'\[VOCAB:([^\]]+)\]', lambda m: f'<b>{html.escape(m.group(1))}</b>', chat_response)
            sent = await message.answer(f"💬 {display_safe}", parse_mode="HTML")
            _cache_original(sent.message_id, chat_response_clean)
            await sent.edit_reply_markup(reply_markup=get_translate_keyboard(sent.message_id))

        # Analysis block: send AFTER the response, only if there's a real correction
        safe_corrected = html.escape(analysis_data.get('corrected_sentence', ''))
        safe_explanation = html.escape(analysis_data.get('explanation', ''))
        has_correction = bool(safe_corrected and safe_corrected.strip() != html.escape(user_text).strip())
        if has_correction or safe_explanation:
            analysis_lines = []
            if has_correction:
                analysis_lines.append(f"✍️ <b>{safe_corrected}</b>")
            if safe_explanation:
                analysis_lines.append(f"💡 {safe_explanation}")
            if analysis_data.get('vocabulary_items'):
                analysis_lines.append("📚 <i>New words added to your vocabulary</i>")
            await message.answer("\n\n".join(analysis_lines), parse_mode="HTML")

        if is_farewell:
            asyncio.create_task(run_summarization(user_id))
            logger.info(f"Farewell detected for user {user_id}, summarization scheduled")

    except Exception as e:
        logger.error(f"Error handling message: {e}")
        await message.answer("Sorry, I encountered an error processing your message.", parse_mode="HTML")

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

        current_markup = callback.message.reply_markup
        from aiogram.utils.keyboard import InlineKeyboardBuilder
        from aiogram.types import InlineKeyboardButton
        builder = InlineKeyboardBuilder()
        builder.row(InlineKeyboardButton(text="🔤 Original", callback_data=f"original_{message_id}"))
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
        builder.row(InlineKeyboardButton(text="🌐 Translate", callback_data=f"translate_{message_id}"))
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

@router.callback_query(F.data.startswith("flow_text_"))
async def flow_show_text(callback: CallbackQuery):
    try:
        message_id = int(callback.data.split("_")[2])
        original = _originals_cache.get(message_id)

        if not original:
            await callback.answer("Text not available.", show_alert=True)
            return

        safe_text = html.escape(original)
        await callback.message.edit_caption(
            caption=f"💬 {safe_text}",
            parse_mode="HTML",
            reply_markup=get_flow_voice_text_keyboard(message_id)
        )
        await callback.answer()
    except Exception as e:
        logger.error(f"Error in flow_text callback: {e}")
        await callback.answer("Error.", show_alert=True)

@router.callback_query(F.data.startswith("flow_translate_"))
async def flow_translate(callback: CallbackQuery):
    try:
        message_id = int(callback.data.split("_")[2])
        original = _originals_cache.get(message_id)

        if not original:
            await callback.answer("Text not available.", show_alert=True)
            return

        translation = await groq_client.translate_text(original)
        safe_translation = html.escape(translation)

        await callback.message.edit_caption(
            caption=f"🌐 {safe_translation}",
            parse_mode="HTML",
            reply_markup=get_flow_voice_translate_keyboard(message_id)
        )
        await callback.answer()
    except Exception as e:
        logger.error(f"Error in flow_translate callback: {e}")
        await callback.answer("Translation failed.", show_alert=True)

@router.callback_query(F.data.startswith("flow_original_"))
async def flow_original(callback: CallbackQuery):
    try:
        message_id = int(callback.data.split("_")[2])
        original = _originals_cache.get(message_id)

        if not original:
            await callback.answer("Text not available.", show_alert=True)
            return

        persona_display = callback.message.caption.split("\n")[0] if callback.message.caption else ""

        await callback.message.edit_caption(
            caption=persona_display,
            reply_markup=get_flow_voice_keyboard(message_id)
        )
        await callback.answer()
    except Exception as e:
        logger.error(f"Error in flow_original callback: {e}")
        await callback.answer("Error.", show_alert=True)

@router.callback_query(F.data.startswith("uvoice_text_"))
async def uvoice_show_text(callback: CallbackQuery):
    try:
        message_id = int(callback.data.split("_")[2])
        user_text = _originals_cache.get(message_id)

        if not user_text:
            await callback.answer("Text not available.", show_alert=True)
            return

        user = await db.get_or_create_user(callback.from_user.id)
        user_level = user.get("level", settings.DEFAULT_USER_LEVEL)

        correction = await groq_client.correct_text(user_text, user_level)

        safe_text = html.escape(user_text)
        safe_corrected = html.escape(correction.get("corrected_sentence", user_text))
        safe_explanation = html.escape(correction.get("explanation", ""))

        text = (
            f"🎤 <i>{safe_text}</i>\n\n"
            f"✅ <b>Correct</b>\n{safe_corrected}\n\n"
            f"💡 <b>Why</b>\n{safe_explanation}"
        )

        await callback.message.edit_text(text, parse_mode="HTML")
        await callback.answer()
    except Exception as e:
        logger.error(f"Error in uvoice_text callback: {e}")
        await callback.answer("Error.", show_alert=True)

@router.callback_query(F.data.startswith("uvoice_translate_"))
async def uvoice_translate(callback: CallbackQuery):
    try:
        message_id = int(callback.data.split("_")[2])
        user_text = _originals_cache.get(message_id)

        if not user_text:
            await callback.answer("Text not available.", show_alert=True)
            return

        translation = await groq_client.translate_text(user_text)
        safe_translation = html.escape(translation)

        await callback.message.edit_text(
            f"🌐 <i>{safe_translation}</i>",
            parse_mode="HTML"
        )
        await callback.answer()
    except Exception as e:
        logger.error(f"Error in uvoice_translate callback: {e}")
        await callback.answer("Translation failed.", show_alert=True)
