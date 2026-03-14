import logging
import html
import asyncio
import re
from aiogram import Router, F
from aiogram.types import Message, BufferedInputFile, CallbackQuery
from aiogram.fsm.context import FSMContext
from src.bot.handlers.states import FlowState, TutorState
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
from src.utils.tg_helpers import safe_edit_text, safe_edit_reply_markup, safe_edit_caption

router = Router()
logger = logging.getLogger(__name__)

# ─── Helpers ──────────────────────────────────────────────────────────────────

def _process_mistake_tags(text: str) -> tuple[str, str]:
    """[MISTAKE:word] → clean text и display text с жирным."""
    clean_text = re.sub(r'\[MISTAKE:([^\]]+)\]', r'\1', text)
    display_text = re.sub(r'\[MISTAKE:([^\]]+)\]', r'<b>\1</b>', text)
    return clean_text, display_text

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

# ─── Summarization ────────────────────────────────────────────────────────────

_summarization_locks: Dict[int, asyncio.Lock] = {}

def _get_summary_lock(user_id: int) -> asyncio.Lock:
    if user_id not in _summarization_locks:
        _summarization_locks[user_id] = asyncio.Lock()
    return _summarization_locks[user_id]

async def run_summarization(user_id: int) -> None:
    lock = _get_summary_lock(user_id)
    if lock.locked():
        return
    async with lock:
        try:
            messages = await db.get_messages_for_summary(user_id, limit=30)
            if not messages:
                return
            existing_summary = await db.get_latest_summary(user_id)
            result = await groq_client.summarize_conversation(messages, existing_summary)
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

# ─── Mode activation ──────────────────────────────────────────────────────────

@router.message(F.text == "🎙 Flow")
async def activate_flow(message: Message, state: FSMContext):
    await state.update_data(active_mode=MODE_FLOW)
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
    await db.update_mode(message.from_user.id, MODE_PENFRIEND)
    await state.update_data(active_mode=MODE_PENFRIEND)
    await state.set_state(FlowState.choosing_persona)
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
        fsm_data = await state.get_data()
        current_mode = fsm_data.get("active_mode", MODE_FLOW)  # берём из FSM, не из БД

        history = await db.get_history(user_id, limit=settings.CONTEXT_WINDOW)
        context_text = " | ".join([
            f"{m['role']}: {m['content']}" for m in history
        ]) if history else ""

        await state.update_data(switch_context=context_text, active_mode=current_mode)
        await state.set_state(FlowState.choosing_persona)
        await message.answer("Who would you like to talk to next?", reply_markup=get_persona_keyboard())
    except Exception as e:
        logger.error(f"Error in flow switch reply: {e}")
        await message.answer("Something went wrong. Try again.")

# ─── Persona selection ────────────────────────────────────────────────────────

@router.callback_query(F.data.startswith("persona_"), FlowState.choosing_persona)
async def flow_persona_selected(callback: CallbackQuery, state: FSMContext):
    try:
        persona_key = callback.data.split("_", 1)[1]
        user_id = callback.from_user.id
        voice = get_persona_voice(persona_key)

        fsm_data = await state.get_data()
        switch_context = fsm_data.get("switch_context", "")
        from_settings = fsm_data.get("from_settings", False)
        active_mode = fsm_data.get("active_mode", MODE_FLOW)  # всегда из FSM

        await db.update_user_persona(user_id, persona_key)
        await db.update_user_voice(user_id, voice)

        if from_settings:
            display_name = get_persona_display(persona_key)
            await state.clear()
            from src.bot.keyboards import get_settings_keyboard
            user = await db.get_or_create_user(user_id)
            notif = user.get("notifications_enabled", True)
            await safe_edit_text(callback.message, 
                f"👤 Now talking to {display_name}.\n\n⚙️ <b>Settings</b>",
                parse_mode="HTML",
                reply_markup=get_settings_keyboard(notif, user_id)
            )
            await callback.answer()
            return

        await state.update_data(
            persona_key=persona_key,
            voice=voice,
            switch_context="",
            active_mode=active_mode,
        )
        await state.set_state(FlowState.active)

        user = await db.get_or_create_user(user_id)
        user_level = user.get("level", "intermediate")
        existing_summary = await db.get_latest_summary(user_id)

        await safe_edit_text(callback.message, "...")

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

        if active_mode == MODE_PENFRIEND:
            mode_keyboard = get_penfriend_keyboard()
            mode_label = f"↩ Switched to {persona_display}" if switch_context else f"✉️ PenFriend Mode — {persona_display}"
        else:
            mode_keyboard = get_flow_stop_keyboard()
            mode_label = f"↩ Switched to {persona_display}" if switch_context else f"🎙 Flow Mode — {persona_display}"

        await callback.message.answer(mode_label, parse_mode="HTML", reply_markup=mode_keyboard)

        if active_mode != MODE_PENFRIEND:
            voice_bytes = await groq_client.text_to_speech(greeting, voice=voice)
            if voice_bytes:
                voice_file = BufferedInputFile(voice_bytes, filename="greeting.wav")
                sent = await callback.message.answer_voice(
                    voice_file,
                    caption=persona_display,
                    reply_markup=get_flow_voice_keyboard(0)
                )
                _cache_original(sent.message_id, greeting)
                await safe_edit_reply_markup(sent, reply_markup=get_flow_voice_keyboard(sent.message_id))
        else:
            safe_greeting = html.escape(greeting)
            sent = await callback.message.answer(f"💬 {safe_greeting}", parse_mode="HTML")
            _cache_original(sent.message_id, greeting)
            await safe_edit_reply_markup(sent, reply_markup=get_translate_keyboard(sent.message_id))

        await callback.answer()

    except Exception as e:
        logger.error(f"Error in flow persona selection: {e}")
        await callback.answer("Something went wrong.", show_alert=True)

# ─── Flow / PenFriend message handler ────────────────────────────────────────

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

                # 👇 НОВЫЙ БЛОК: ЗАЩИТА FLOW MODE 👇
        if active_mode == MODE_FLOW and message.text:
            # Пропускаем нажатия на кнопки меню и команды, чтобы бот не ругался на них
            BUTTON_TEXTS = {"🎓 Tutor", "✉️ PenFriend", "🎙 Flow", "⏹ Stop Flow", "⏹ Stop Tutor", "⏹ Stop PenFriend", "↩ Switch"}
            if message.text.strip() in BUTTON_TEXTS or message.text.startswith("/"):
                return
            
            # Если это обычный текст — бьем по рукам
            await message.answer(
                "🎙 <b>Flow Mode is a voice-only zone!</b>\n\n"
                "This mode is designed to help you break the language barrier. "
                "No typing allowed — just hold the microphone icon and speak your mind!\n\n"
                "<i>(If you prefer texting, please switch to PenFriend or Tutor mode)</i>",
                parse_mode="HTML"
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
        practice_error_task = asyncio.create_task(db.get_error_for_practice(user_id))

        history, summary, is_farewell, top_errors, practice_error = await asyncio.gather(
            history_task, summary_task, farewell_task, errors_task, practice_error_task
        )

        await message.bot.send_chat_action(user_id, "record_voice")

        # Mistakes practice: передаём одну ошибку в промпт — модель вплетает правильную форму
        extra_instruction = ""
        if practice_error:
            cat = practice_error.get("category", "")
            corrected = practice_error.get("corrected_text", "")
            if corrected:
                extra_instruction = (
                    f"# MISTAKES PRACTICE\n"
                    f"The user struggles with: {cat}.\n"
                    f"Example of their mistake corrected: \"{corrected}\"\n"
                    f"Naturally use the correct form of this pattern in your response. "
                    f"Wrap the corrected form in [MISTAKE:...] tags so it can be highlighted. "
                    f"Example: [MISTAKE:I have been waiting] for hours. "
                    f"Do NOT announce this. Just model it naturally. One instance only."
                )

        chat_response = await groq_client.generate_flow_response(
            text=user_text,
            persona_key=persona_key,
            history=history,
            summary=summary,
            session_count=user.get("session_count", 0),
            top_errors=top_errors,
            extra_instruction=extra_instruction
        )

        # Детектим правильное употребление — увеличиваем mastery ошибки
        if practice_error and practice_error.get("corrected_text"):
            asyncio.create_task(
                _check_and_update_error_mastery(user_id, user_text, practice_error)
            )

        chat_response_clean, chat_response_display = _process_mistake_tags(chat_response)
        await db.save_message(user_id, "assistant", chat_response_clean)

        persona_display = get_persona_display(persona_key)

        if active_mode == MODE_PENFRIEND:
            delay = _penfriend_typing_delay(chat_response_clean)
            await message.bot.send_chat_action(user_id, "typing")
            await asyncio.sleep(delay)
            sent = await message.answer(f"💬 {chat_response_display}", parse_mode="HTML")
            _cache_original(sent.message_id, chat_response_clean)
            await safe_edit_reply_markup(sent, reply_markup=get_translate_keyboard(sent.message_id))
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
                await safe_edit_reply_markup(sent, reply_markup=get_flow_voice_keyboard(sent.message_id))

        if is_farewell:
            asyncio.create_task(run_summarization(user_id))
            asyncio.create_task(db.increment_session_count(user_id))
            logger.info(f"Farewell detected for user {user_id}, summarization scheduled")

    except Exception as e:
        logger.error(f"Error in flow message: {e}")
        await message.answer("Something went wrong. Try again.")

# ─── Tutor Mode message handler ───────────────────────────────────────────────


@router.message(TutorState.awaiting_drill)
async def handle_drill_attempt(message: Message, state: FSMContext, user: Dict[str, Any] = None):
    """Обрабатывает попытку юзера повторить исправленное предложение."""
    try:
        user_id = message.from_user.id
        if user is None:
            user = await db.get_or_create_user(user_id)

        if message.voice:
            await message.bot.send_chat_action(user_id, "typing")
            voice_file = await message.bot.get_file(message.voice.file_id)
            voice_bytes = await message.bot.download_file(voice_file.file_path)
            attempt_text = await transcribe_voice_with_groq(voice_bytes.read())
            if not attempt_text:
                await message.answer("Couldn't hear that clearly. Try again.")
                return
            safe_said = html.escape(attempt_text)
            await message.answer(f"🎤 <i>You said:</i> {safe_said}", parse_mode="HTML")
        elif message.text:
            attempt_text = message.text.strip()
            BUTTON_TEXTS = {"🎓 Tutor", "✉️ PenFriend", "🎙 Flow", "⏹ Stop Flow", "⏹ Stop Tutor", "⏹ Stop PenFriend", "↩ Switch"}
            if attempt_text in BUTTON_TEXTS or attempt_text.startswith("/"):
                await state.clear()
                return
        else:
            return

        fsm_data = await state.get_data()
        drill_mistake = fsm_data.get("drill_mistake", "")
        drill_target = fsm_data.get("drill_target", "")
        drill_attempts = fsm_data.get("drill_attempts", 0)

        result = await groq_client.evaluate_drill(
            original_mistake=drill_mistake,
            corrected_target=drill_target,
            student_attempt=attempt_text,
        )

        success = result.get("success", False)
        feedback = html.escape(result.get("feedback", "Good effort!"))

        if success:
            # Засчитываем mastery если есть активная ошибка
            practice_error = await db.get_error_for_practice(user_id)
            if practice_error:
                asyncio.create_task(db.increase_error_mastery(practice_error["id"]))
            await message.answer(f"✅ {feedback}", parse_mode="HTML")
            await state.clear()
        else:
            drill_attempts += 1
            if drill_attempts >= 2:
                # После 2 неудачных попыток — отпускаем без давления
                await message.answer(
                    f"💬 {feedback}\n\n<i>We'll keep working on it — no rush.</i>",
                    parse_mode="HTML"
                )
                await state.clear()
            else:
                await state.update_data(drill_attempts=drill_attempts)
                await message.answer(f"💬 {feedback}", parse_mode="HTML")

    except Exception as e:
        logger.error(f"Error in drill attempt: {e}")
        await state.clear()
        await message.answer("Let's keep going.", parse_mode="HTML")

@router.message()
async def handle_message(message: Message, state: FSMContext, user: Dict[str, Any] = None, is_admin: bool = False):
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
        practice_error_task = asyncio.create_task(db.get_error_for_practice(user_id))

        history, is_farewell, summary, topics, practice_error = await asyncio.gather(
            history_task, farewell_task, summary_task, topics_task, practice_error_task
        )

        user_level = user.get("level", settings.DEFAULT_USER_LEVEL)
        should_reply_voice = (
            settings.VOICE_RESPONSE_MODE == "always" or
            (settings.VOICE_RESPONSE_MODE == "mirror" and is_voice_input)
        )

        await message.bot.send_chat_action(user_id, "typing")
        chat_response, analysis_data = await groq_client.process_user_message(
            telegram_id=user_id,
            user_text=user_text,
            user_level=user_level,
            history=history,
            summary=summary,
            topics=topics,
            practice_error=practice_error if user.get('mistakes_practice_enabled') else None,
        )

        # Детектим правильное употребление — увеличиваем mastery ошибки
        if practice_error and practice_error.get("corrected_text"):
            asyncio.create_task(
                _check_and_update_error_mastery(user_id, user_text, practice_error)
            )

        # Логируем новую ошибку с corrected_text
        error_cat = analysis_data.get("error_category")
        if error_cat and error_cat.lower() != "none":
            asyncio.create_task(db.log_error(user_id, {
                "category": error_cat,
                "mistake_text": user_text,
                "corrected_text": analysis_data.get("corrected_sentence", ""),
            }))

        await db.increment_user_metrics(user_id, tokens_used=0)

        chat_response_clean, chat_response_display = _process_mistake_tags(chat_response)
        await db.save_message(user_id, "assistant", chat_response_clean)

        if not chat_response_clean:
            chat_response_clean = "Sorry, I couldn't formulate a response."
            chat_response_display = chat_response_clean

        voice = user.get("voice") or settings.TTS_VOICE
        persona_display = get_persona_display(user.get("persona", "mrs_smith"))

        if should_reply_voice:
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
                await safe_edit_reply_markup(sent_voice, reply_markup=get_flow_voice_keyboard(sent_voice.message_id))
        else:
            sent = await message.answer(f"💬 {chat_response_display}", parse_mode="HTML")
            _cache_original(sent.message_id, chat_response_clean)
            await safe_edit_reply_markup(sent, reply_markup=get_translate_keyboard(sent.message_id))

        # Блок коррекции — отдельным сообщением после ответа
        raw_corrected = analysis_data.get("corrected_sentence", "")
        raw_explanation = analysis_data.get("explanation", "")
        safe_corrected = html.escape(raw_corrected)
        safe_explanation = html.escape(raw_explanation)
        has_correction = bool(safe_corrected and safe_corrected.strip() != html.escape(user_text).strip())
        if has_correction or safe_explanation:
            analysis_lines = []
            if has_correction:
                analysis_lines.append(f"✍️ <b>{safe_corrected}</b>")
            if safe_explanation:
                analysis_lines.append(f"💡 {safe_explanation}")
            await message.answer("\n\n".join(analysis_lines), parse_mode="HTML")

        # Drill — предлагаем повторить если была реальная ошибка
        if has_correction and raw_corrected and raw_explanation:
            drill_invite = await groq_client.generate_drill_invite(
                user_text=user_text,
                corrected_sentence=raw_corrected,
                explanation=raw_explanation,
            )
            await message.answer(f"🔁 {html.escape(drill_invite)}", parse_mode="HTML")
            await state.set_state(TutorState.awaiting_drill)
            await state.update_data(
                drill_mistake=user_text,
                drill_target=raw_corrected,
                drill_attempts=0,
            )

        if is_farewell:
            asyncio.create_task(run_summarization(user_id))
            logger.info(f"Farewell detected for user {user_id}, summarization scheduled")

    except Exception as e:
        logger.error(f"Error handling message: {e}")
        await message.answer("Sorry, I encountered an error processing your message.", parse_mode="HTML")

# ─── Error mastery detection ──────────────────────────────────────────────────

async def _check_and_update_error_mastery(user_id: int, user_text: str, practice_error: Dict[str, Any]) -> None:
    """Фоновая задача: если юзер правильно употребил конструкцию — увеличиваем mastery."""
    try:
        corrected_text = practice_error.get("corrected_text", "")
        if not corrected_text:
            return
        used_correctly = await groq_client.detect_word_usage(corrected_text, user_text)
        if used_correctly:
            new_score = await db.increase_error_mastery(practice_error["id"])
            logger.info(f"✅ Error mastery increased for user {user_id}: {practice_error.get('category')} → {new_score}")
    except Exception as e:
        logger.error(f"Error in mastery check: {e}")

# ─── Translate / Original callbacks ──────────────────────────────────────────

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

        from aiogram.utils.keyboard import InlineKeyboardBuilder
        from aiogram.types import InlineKeyboardButton
        builder = InlineKeyboardBuilder()
        builder.row(InlineKeyboardButton(text="🔤 Original", callback_data=f"original_{message_id}"))
        current_markup = callback.message.reply_markup
        if current_markup:
            for row in current_markup.inline_keyboard:
                for btn in row:
                    if "translate_" not in btn.callback_data and "original_" not in btn.callback_data:
                        builder.row(btn)

        await safe_edit_text(callback.message, 
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

        from aiogram.utils.keyboard import InlineKeyboardBuilder
        from aiogram.types import InlineKeyboardButton
        builder = InlineKeyboardBuilder()
        builder.row(InlineKeyboardButton(text="🌐 Translate", callback_data=f"translate_{message_id}"))
        current_markup = callback.message.reply_markup
        if current_markup:
            for row in current_markup.inline_keyboard:
                for btn in row:
                    if "translate_" not in btn.callback_data and "original_" not in btn.callback_data:
                        builder.row(btn)

        await safe_edit_text(callback.message, 
            f"💬 {safe_original}",
            parse_mode="HTML",
            reply_markup=builder.as_markup()
        )
        await callback.answer()
    except Exception as e:
        logger.error(f"Error in original callback: {e}")
        await callback.answer("Could not restore original.", show_alert=True)

# ─── Flow voice callbacks ──────────────────────────────────────────────────────

@router.callback_query(F.data.startswith("flow_text_"))
async def flow_show_text(callback: CallbackQuery):
    try:
        message_id = int(callback.data.split("_")[2])
        original = _originals_cache.get(message_id)
        if not original:
            await callback.answer("Text not available.", show_alert=True)
            return
        safe_text = html.escape(original)
        await safe_edit_caption(callback.message, 
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
        await safe_edit_caption(callback.message, 
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
        await safe_edit_caption(callback.message, 
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
        await safe_edit_text(callback.message, text, parse_mode="HTML")
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
        await safe_edit_text(callback.message, 
            f"🌐 <i>{safe_translation}</i>",
            parse_mode="HTML"
        )
        await callback.answer()
    except Exception as e:
        logger.error(f"Error in uvoice_translate callback: {e}")
        await callback.answer("Translation failed.", show_alert=True)
