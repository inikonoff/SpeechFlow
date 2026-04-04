import logging
import html
import asyncio
import re
from aiogram import Router, F
from aiogram.types import Message, BufferedInputFile, CallbackQuery
from aiogram.fsm.context import FSMContext
from src.bot.handlers.states import FlowState
from typing import Dict, Any

from src.config import settings, ADMIN_IDS
from src.services.supabase_db import db
from src.services.groq_client import groq_client
from src.utils.audio import save_voice_file, cleanup_file, read_file_bytes
from src.personas import get_persona_voice, get_persona_display
from src.bot.keyboards import (
    get_translate_keyboard,
    get_original_keyboard,
    get_flow_stop_keyboard,
    get_flow_voice_keyboard,
    get_flow_voice_text_keyboard,
    get_flow_voice_translate_keyboard,
    get_flow_user_voice_keyboard,
    get_flow_user_voice_text_keyboard,
    get_flow_user_voice_translate_keyboard,
    get_persona_keyboard,
    get_mode_keyboard,
    get_penfriend_keyboard,
)
from src.modes import MODE_TUTOR, MODE_PENFRIEND, MODE_FLOW
from src.utils.tg_helpers import safe_edit_text, safe_edit_reply_markup, safe_edit_caption

router = Router()
logger = logging.getLogger(__name__)

# ─── Helpers ──────────────────────────────────────────────────────────────────

_originals_cache: Dict[int, str] = {}
_display_cache: Dict[int, str] = {}  # HTML-версия с <b> для показа
_translation_cache: Dict[int, str] = {}  # кэш переводов
SUMMARY_MERGE_THRESHOLD = 4
BUTTON_TEXTS = {
    "🎓 Tutor", "✉️ PenFriend", "🎙 Flow",
    "⏹ Stop Flow", "⏹ Stop Tutor", "⏹ Stop PenFriend", "↩ Switch"
}

_persona_display_cache: Dict[int, str] = {}

def _cache_original(msg_id: int, text: str):
    if len(_originals_cache) > 5000:
        _originals_cache.clear()
    _originals_cache[msg_id] = text

def _cache_translation(msg_id: int, text: str):
    if len(_translation_cache) > 5000:
        _translation_cache.clear()
    _translation_cache[msg_id] = text

def _cache_display(msg_id: int, text: str):
    if len(_display_cache) > 5000:
        _display_cache.clear()
    _display_cache[msg_id] = text

def _cache_persona_display(msg_id: int, persona_display: str):
    if len(_persona_display_cache) > 5000:
        _persona_display_cache.clear()
    _persona_display_cache[msg_id] = persona_display

def _md_bold_to_html(text: str) -> str:
    """Конвертирует **bold** markdown в <b>bold</b> HTML для Telegram."""
    return re.sub(r'\*\*(.+?)\*\*', r'<b>\1</b>', text, flags=re.DOTALL)

def _penfriend_typing_delay(text: str) -> float:
    words = len(text.split())
    seconds = (words / 55) * 60 * 0.7
    return max(1.4, min(seconds, 6.3))

async def transcribe_voice_with_groq(voice_file_bytes: bytes) -> str:
    try:
        ogg_path = await save_voice_file(voice_file_bytes, "ogg")
        try:
            audio_bytes = await read_file_bytes(ogg_path)
            text = await groq_client.transcribe_audio(audio_bytes)
            # Убираем артефакты Whisper
            if text:
                import re as _re
                text = _re.sub(
                    r'[.\s]*(Subtitles by[^.]*[.]?|Amara[.]org[^.]*[.]?|Translated by[^.]*[.]?|transcribed by[^.]*[.]?)$',
                    '', text, flags=_re.IGNORECASE
                ).strip()
            return text
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

# ─── Flow Mode: фоновый анализ ошибок ─────────────────────────────────────────

async def _background_flow_error_check(user_id: int, user_text: str, user_level: str) -> None:
    """
    Запускается через asyncio.create_task — юзер ничего не видит.
    Анализирует текст на ошибки и тихо пишет в БД для Sunday Deep Dive.
    """
    try:
        result = await groq_client.check_flow_errors(user_text, user_level)
        category = result.get("error_category", "none")
        corrected = result.get("corrected_sentence", "")
        if category and category.lower() != "none" and corrected:
            await db.log_flow_error(user_id, {
                "category": category,
                "mistake_text": user_text,
                "corrected_text": corrected,
            })
    except Exception as e:
        logger.error(f"Error in background flow error check for user {user_id}: {e}")

# ─── Автооценка уровня (fire-and-forget) ─────────────────────────────────────

LEVEL_ORDER = ["beginner", "elementary", "intermediate", "advanced"]

async def _run_level_assessment(user_id: int, current_level: str, message: Message) -> None:
    """
    Запускается каждые 10 сообщений пользователя через asyncio.create_task.
    Если assessed_level выше current_level и confidence == 'high' →
    Mrs. Smith органично предлагает повысить уровень одним сообщением в чат.
    Никогда не меняет уровень автоматически.
    """
    try:
        history = await db.get_history(user_id, limit=20)
        if not history:
            return

        result = await groq_client.assess_user_level(history, current_level)
        assessed = result.get("assessed_level", current_level)
        confidence = result.get("confidence", "low")

        if confidence != "high":
            return

        try:
            current_rank = LEVEL_ORDER.index(current_level.lower())
            assessed_rank = LEVEL_ORDER.index(assessed.lower())
        except ValueError:
            return

        if assessed_rank <= current_rank:
            return

        # Уровень выше и уверенность высокая → Mrs. Smith предлагает повысить
        assessed_label = assessed.capitalize()
        current_label = current_level.capitalize()
        suggestion = (
            f"📚 <i>Mrs. Smith noticed something:</i>\n\n"
            f"Your English has been sounding more like <b>{assessed_label}</b> lately. "
            f"You're still set to {current_label} — would you like to move up?\n\n"
            f"You can change your level anytime in /settings."
        )
        await message.answer(suggestion, parse_mode="HTML")
        logger.info(f"Level assessment for user {user_id}: {current_level} → {assessed} (high confidence)")

    except Exception as e:
        logger.error(f"Error in _run_level_assessment for user {user_id}: {e}")

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
    await message.answer(
        "🎓 Tutor Mode on — 📚 Mrs. Smith",
        parse_mode="HTML",
        reply_markup=get_mode_keyboard()
    )

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
        current_mode = fsm_data.get("active_mode", MODE_FLOW)

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
        active_mode = fsm_data.get("active_mode", MODE_FLOW)

        await db.update_user_persona(user_id, persona_key)
        await db.update_user_voice(user_id, voice)

        if from_settings:
            display_name = get_persona_display(persona_key)
            await state.clear()
            from src.bot.keyboards import get_settings_keyboard
            user = await db.get_or_create_user(user_id)
            notif = user.get("notifications_enabled", True)
            recasting = user.get("recasting_enabled", False)
            practice = user.get("mistakes_practice_enabled", False)
            await safe_edit_text(
                callback.message,
                f"👤 Now talking to {display_name}.\n\n⚙️ <b>Settings</b>",
                parse_mode="HTML",
                reply_markup=get_settings_keyboard(notif, recasting, practice, user_id)
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
                _cache_persona_display(sent.message_id, persona_display)
                await safe_edit_reply_markup(sent, reply_markup=get_flow_voice_keyboard(sent.message_id))
        else:
            safe_greeting = html.escape(greeting)
            sent = await callback.message.answer(f"💬 {safe_greeting}\n\n<i>{persona_display}</i>", parse_mode="HTML")
            _cache_original(sent.message_id, greeting)
            _cache_persona_display(sent.message_id, persona_display)
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

        # PenFriend — только текст
        if active_mode == MODE_PENFRIEND and message.voice:
            await message.answer(
                "✉️ PenFriend is text-only.\n"
                "Try typing what you wanted to say — it's good writing practice too."
            )
            return

        # Flow — только голос
        if active_mode == MODE_FLOW and message.text:
            if message.text.strip() in BUTTON_TEXTS or message.text.startswith("/"):
                return
            await message.answer(
                "🎙 <b>Flow Mode is voice-only!</b>\n\n"
                "Hold the microphone icon and speak. "
                "For text practice, switch to PenFriend or Tutor.",
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

        # Юзер вернулся — сбрасываем счётчик re-engagement фоново
        if user.get("reengagement_count", 0):
            asyncio.create_task(db.reset_reengagement(user_id))

        # Автооценка уровня каждые 10 сообщений — fire-and-forget
        msg_count = await db.get_user_message_count(user_id)
        if msg_count > 0 and msg_count % 10 == 0:
            asyncio.create_task(
                _run_level_assessment(user_id, user_level, message)
            )

        farewell_task = asyncio.create_task(groq_client.detect_farewell(user_text))
        history_task = asyncio.create_task(db.get_history(user_id, limit=settings.CONTEXT_WINDOW))
        summary_task = asyncio.create_task(db.get_latest_summary(user_id))
        errors_task = asyncio.create_task(db.get_top_error_categories(user_id, limit=2))

        history, summary, is_farewell, top_errors = await asyncio.gather(
            history_task, summary_task, farewell_task, errors_task
        )

        await message.bot.send_chat_action(user_id, "record_voice")

        if active_mode == MODE_PENFRIEND:
            # Читаем юзера заново — значение из middleware может быть устаревшим
            # если тоггл был нажат в этой же сессии
            fresh_user = await db.get_or_create_user(user_id)
            recasting_enabled = fresh_user.get("recasting_enabled", False)
            pf_practice_error = None
            if fresh_user.get("mistakes_practice_enabled"):
                recent = await db.get_recent_errors(user_id, limit=10)
                if recent:
                    import random
                    pf_practice_error = random.choice(recent)
            pf_bubbles = await groq_client.generate_penfriend_multibubble(
                text=user_text,
                persona_key=persona_key,
                history=history,
                summary=summary,
                recasting_enabled=recasting_enabled,
                practice_error=pf_practice_error,
            )
            # Используем последнее сообщение как chat_response для сохранения в БД
            chat_response = " ".join(pf_bubbles)
        else:
            # Flow Mode: генерируем ответ и запускаем фоновый анализ ошибок
            practice_error = None
            if user.get("mistakes_practice_enabled"):
                recent = await db.get_recent_errors(user_id, limit=10)
                if recent:
                    import random
                    practice_error = random.choice(recent)
            chat_response = await groq_client.generate_flow_response(
                text=user_text,
                persona_key=persona_key,
                history=history,
                summary=summary,
                session_count=user.get("session_count", 0),
                top_errors=top_errors,
                practice_error=practice_error,
            )
            # Фоновая задача — тихо пишет ошибки в БД, юзер не ждёт
            asyncio.create_task(
                _background_flow_error_check(user_id, user_text, user_level)
            )

        await db.save_message(user_id, "assistant", chat_response)
        persona_display = get_persona_display(persona_key)

        if active_mode == MODE_PENFRIEND:
            # Мультибабл: последовательная отправка с typing-задержками
            # Translate только под последним сообщением — переводит ВСЕ баблы этого ответа

            # Готовим объединённый текст всех баблов для кэша (вариант Б)
            # _originals_cache хранит сырой текст БЕЗ **bold** — чтобы не путать LLM-акцент с recasting
            all_bubbles_raw = "\n\n".join(
                re.sub(r'\*\*(.+?)\*\*', r'\1', b) for b in pf_bubbles
            )
            all_bubbles_display = "\n\n".join(
                _md_bold_to_html(html.escape(b)) for b in pf_bubbles
            )

            for i, bubble in enumerate(pf_bubbles):
                is_last = (i == len(pf_bubbles) - 1)
                delay = _penfriend_typing_delay(bubble)
                await message.bot.send_chat_action(user_id, "typing")
                await asyncio.sleep(delay)
                safe_bubble = html.escape(bubble)
                display_bubble = _md_bold_to_html(safe_bubble)
                if is_last:
                    sent = await message.answer(
                        f"💬 {display_bubble}\n\n<i>{persona_display}</i>",
                        parse_mode="HTML"
                    )
                    # Кэшируем объединённый текст всех баблов — Translate покажет весь ответ
                    _cache_original(sent.message_id, all_bubbles_raw)
                    _cache_display(sent.message_id, all_bubbles_display)
                    _cache_persona_display(sent.message_id, persona_display)
                    await safe_edit_reply_markup(sent, reply_markup=get_translate_keyboard(sent.message_id))
                else:
                    await message.answer(f"💬 {display_bubble}", parse_mode="HTML")
        else:
            voice_bytes = await groq_client.text_to_speech(chat_response, voice=voice)
            if voice_bytes:
                voice_file = BufferedInputFile(voice_bytes, filename="response.wav")
                sent = await message.answer_voice(
                    voice_file,
                    caption=persona_display,
                    reply_markup=get_flow_voice_keyboard(0)
                )
                _cache_original(sent.message_id, chat_response)
                _cache_persona_display(sent.message_id, persona_display)
                await safe_edit_reply_markup(sent, reply_markup=get_flow_voice_keyboard(sent.message_id))

        if is_farewell:
            asyncio.create_task(run_summarization(user_id))
            asyncio.create_task(db.increment_session_count(user_id))
            logger.info(f"Farewell detected for user {user_id}, summarization scheduled")

    except Exception as e:
        logger.error(f"Error in flow message: {e}")
        await message.answer("Something went wrong. Try again.")

# ─── Tutor Mode (handle_message) ─────────────────────────────────────────────

@router.message()
async def handle_message(message: Message, state: FSMContext, user: Dict[str, Any] = None, is_admin: bool = False):
    """
    Tutor Mode + дефолтный обработчик.
    Два параллельных LLM потока:
      1. correct_text → карточка ошибки (❌ / ✅ / 💡)
      2. generate_tutor_response → ответ Mrs. Smith по смыслу
    Юзер получает оба баббла почти одновременно.
    """
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
            sent_user = await message.answer(
                f"🎤 You said: {safe_text}",
                parse_mode="HTML",
                reply_markup=get_flow_user_voice_keyboard(0)
            )
            _cache_original(sent_user.message_id, user_text)
            await safe_edit_reply_markup(
                sent_user,
                reply_markup=get_flow_user_voice_keyboard(sent_user.message_id)
            )

        elif message.text:
            user_text = message.text.strip()
            if not user_text or user_text.startswith("/") or user_text in BUTTON_TEXTS:
                return
        elif message.audio or message.video_note:
            await message.answer("I only understand standard voice messages and text for now.")
            return
        else:
            return

        user_level = user.get("level", settings.DEFAULT_USER_LEVEL)
        await db.save_message(user_id, "user", user_text)

        # Юзер вернулся — сбрасываем счётчик re-engagement фоново
        if user.get("reengagement_count", 0):
            asyncio.create_task(db.reset_reengagement(user_id))

        # Автооценка уровня каждые 10 сообщений — fire-and-forget
        msg_count = await db.get_user_message_count(user_id)
        if msg_count > 0 and msg_count % 10 == 0:
            asyncio.create_task(
                _run_level_assessment(user_id, user_level, message)
            )

        # Параллельно: история + саммари + прощание
        farewell_task = asyncio.create_task(groq_client.detect_farewell(user_text))
        history_task = asyncio.create_task(db.get_history(user_id, limit=settings.CONTEXT_WINDOW))
        summary_task = asyncio.create_task(db.get_latest_summary(user_id))
        topics_task = asyncio.create_task(db.get_topics_to_discuss(user_id))

        history, is_farewell, summary, topics = await asyncio.gather(
            history_task, farewell_task, summary_task, topics_task
        )

        await message.bot.send_chat_action(user_id, "typing")

        # Два LLM параллельно: ответ + коррекция
        tutor_practice_error = None
        if user.get("mistakes_practice_enabled"):
            recent = await db.get_recent_errors(user_id, limit=10)
            if recent:
                import random
                tutor_practice_error = random.choice(recent)
        chat_response, analysis_data = await groq_client.process_user_message(
            telegram_id=user_id,
            user_text=user_text,
            user_level=user_level,
            history=history,
            summary=summary,
            topics=topics,
            practice_error=tutor_practice_error,
            message_count=len(history) if history else 0,
        )

        await db.save_message(user_id, "assistant", chat_response)
        await db.increment_user_metrics(user_id, tokens_used=0)

        # ── Бабл 1: карточка ошибки (только если ошибка реальная) ──────────
        # TODO: в следующей итерации — прятать карточку под Telegram-спойлер
        raw_corrected = analysis_data.get("corrected_sentence", "")
        raw_explanation = analysis_data.get("explanation", "")
        error_category = analysis_data.get("error_category", "none")

        has_real_error = (
            error_category and
            error_category.lower() != "none" and
            raw_corrected and
            raw_corrected.strip().lower() != user_text.strip().lower()
        )

        if has_real_error:
            safe_original = html.escape(user_text)
            safe_corrected = html.escape(raw_corrected)
            safe_explanation = html.escape(raw_explanation) if raw_explanation else ""

            spoiler_lines = [
                f"❌ {safe_original}",
                f"✅ {safe_corrected}",
            ]
            if safe_explanation:
                spoiler_lines.append(f"💡 {safe_explanation}")

            spoiler_content = "\n".join(spoiler_lines)
            await message.answer(
                f"<blockquote expandable>{spoiler_content}</blockquote>",
                parse_mode="HTML"
            )

            # Тихо логируем в БД для статистики
            asyncio.create_task(db.log_tutor_error(user_id, {
                "category": error_category,
                "mistake_text": user_text,
                "corrected_text": raw_corrected,
            }))

        # ── Бабл 2: ответ Mrs. Smith ──────────────────────────────────────
        voice = user.get("voice") or settings.TTS_VOICE
        should_reply_voice = (
            settings.VOICE_RESPONSE_MODE == "always" or
            (settings.VOICE_RESPONSE_MODE == "mirror" and is_voice_input)
        )

        if should_reply_voice:
            await message.bot.send_chat_action(user_id, "record_voice")
            voice_bytes_out = await groq_client.text_to_speech(chat_response, voice=voice)
            if voice_bytes_out:
                persona_display = get_persona_display(user.get("persona", "mrs_smith"))
                voice_file_out = BufferedInputFile(voice_bytes_out, filename="response.wav")
                sent_voice = await message.answer_voice(
                    voice_file_out,
                    caption=persona_display,
                    reply_markup=get_flow_voice_keyboard(0)
                )
                _cache_original(sent_voice.message_id, chat_response)
                await safe_edit_reply_markup(
                    sent_voice,
                    reply_markup=get_flow_voice_keyboard(sent_voice.message_id)
                )
        else:
            safe_response = html.escape(chat_response)
            display_response = _md_bold_to_html(safe_response)
            persona_display = get_persona_display(user.get("persona", "mrs_smith"))
            sent = await message.answer(f"💬 {display_response}\n\n<i>{html.escape(persona_display)}</i>", parse_mode="HTML")
            _cache_original(sent.message_id, chat_response)
            _cache_display(sent.message_id, display_response)
            _cache_persona_display(sent.message_id, persona_display)
            await safe_edit_reply_markup(sent, reply_markup=get_translate_keyboard(sent.message_id))

        if is_farewell:
            asyncio.create_task(run_summarization(user_id))
            logger.info(f"Farewell detected for user {user_id}, summarization scheduled")

    except Exception as e:
        logger.error(f"Error handling message: {e}")
        await message.answer("Sorry, I encountered an error processing your message.", parse_mode="HTML")

# ─── Translate / Original callbacks ──────────────────────────────────────────

@router.callback_query(F.data.startswith("translate_"))
async def handle_translate(callback: CallbackQuery):
    try:
        message_id = int(callback.data.split("_")[1])
        original_text = _originals_cache.get(message_id)

        if not original_text:
            raw = callback.message.text or ""
            original_text = raw.removeprefix("💬 ").strip()

        # Берём из кэша если уже переводили это сообщение
        cached_translation = _translation_cache.get(message_id)
        if cached_translation:
            safe_translation = cached_translation
        else:
            # Передаём оригинал с **bold** — LLM сохранит маркеры вокруг перевода
            translation = await groq_client.translate_text(original_text)
            # Конвертируем **bold** → <b>bold</b>, остальное экранируем
            safe_translation = _md_bold_to_html(html.escape(translation))
            _cache_translation(message_id, safe_translation)

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

        persona_disp = _persona_display_cache.get(message_id, "")
        translation_body = f"🌐 {safe_translation}\n\n<i>{html.escape(persona_disp)}</i>" if persona_disp else f"🌐 {safe_translation}"
        await safe_edit_text(
            callback.message,
            translation_body,
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

        # Берём display-версию из кэша (с <b> тегами), или конвертируем на лету
        display_text = _display_cache.get(message_id)
        safe_original = display_text if display_text else _md_bold_to_html(html.escape(original_text))

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

        persona_disp = _persona_display_cache.get(message_id, "")
        original_body = f"💬 {safe_original}\n\n<i>{html.escape(persona_disp)}</i>" if persona_disp else f"💬 {safe_original}"
        await safe_edit_text(
            callback.message,
            original_body,
            parse_mode="HTML",
            reply_markup=builder.as_markup()
        )
        await callback.answer()
    except Exception as e:
        logger.error(f"Error in original callback: {e}")
        await callback.answer("Could not restore original.", show_alert=True)

@router.callback_query(F.data.startswith("uvoice_original_"))
async def uvoice_original(callback: CallbackQuery):
    try:
        message_id = int(callback.data.split("_")[2])
        user_text = _originals_cache.get(message_id)
        if not user_text:
            await callback.answer("Text not available.", show_alert=True)
            return
        safe_text = html.escape(user_text)
        await safe_edit_text(
            callback.message,
            f"🎤 {safe_text}",
            parse_mode="HTML",
            reply_markup=get_flow_user_voice_text_keyboard(message_id)
        )
        await callback.answer()
    except Exception as e:
        logger.error(f"Error in uvoice_original callback: {e}")
        await callback.answer("Error.", show_alert=True)

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
        persona_disp = _persona_display_cache.get(message_id, "")
        caption_text = f"💬 {safe_text}\n\n<i>{html.escape(persona_disp)}</i>" if persona_disp else f"💬 {safe_text}"
        await safe_edit_caption(
            callback.message,
            caption=caption_text,
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
        await safe_edit_caption(
            callback.message,
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
        persona_display = _persona_display_cache.get(message_id, "")
        await safe_edit_caption(
            callback.message,
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
        safe_text = html.escape(user_text)
        await safe_edit_text(
            callback.message,
            f"🎤 {safe_text}",
            parse_mode="HTML",
            reply_markup=get_flow_user_voice_text_keyboard(message_id)
        )
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
        cached = _translation_cache.get(message_id)
        if cached:
            safe_translation = cached
        else:
            translation = await groq_client.translate_text(user_text)
            safe_translation = html.escape(translation)
            _cache_translation(message_id, safe_translation)
        await safe_edit_text(
            callback.message,
            f"🌐 {safe_translation}",
            parse_mode="HTML",
            reply_markup=get_flow_user_voice_translate_keyboard(message_id)
        )
        await callback.answer()
    except Exception as e:
        logger.error(f"Error in uvoice_translate callback: {e}")
        await callback.answer("Translation failed.", show_alert=True)
