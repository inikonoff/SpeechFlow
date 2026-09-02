# CHANGELOG: 2026-04-12
# - pf_bubbles: добавлен парсинг маркера __RECAST__correct_word__RECAST__
# - correct_word выделяется <b>bold</b> в тексте бабла (первое вхождение)
# - recast_phrases передаются переводчику для bold в переводе
import logging
import html
import asyncio
import re
from aiogram import Router, F
from aiogram.types import Message, BufferedInputFile, CallbackQuery
from aiogram.fsm.context import FSMContext
from src.bot.handlers.states import FlowState, SynonymStreakState
from typing import Dict, Any, Optional

from src.config import settings, ADMIN_IDS, has_feature, get_available_personas
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
    get_tutor_keyboard,
    get_penfriend_keyboard,
    get_synonym_streak_keyboard,
    get_paywall_period_keyboard,
    get_session_summary_offer_keyboard,
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
# Telegram отклоняет edit_caption с MEDIA_CAPTION_TOO_LONG выше этого предела
# (в отличие от текстовых сообщений, лимит на подпись к медиа — 1024, а не
# 4096). Session Summary и длинные переводы легко его превышают.
TELEGRAM_CAPTION_LIMIT = 1024
BUTTON_TEXTS = {
    "🎓 Tutor", "✉️ PenFriend", "🎙 Flow", "🔄 Synonym Streak",
    "⏹ Stop Flow", "⏹ Stop Tutor", "⏹ Stop PenFriend", "↩ Switch",
    "⏹ Stop Synonym Streak", "🔄 New word",
}

_persona_display_cache: Dict[int, str] = {}
_bubble_group_cache: Dict[int, list] = {}  # last_msg_id → [prev_msg_ids] для удаления при переводе

def _cache_original(msg_id: int, text: str):
    if len(_originals_cache) > 5000:
        _originals_cache.clear()
    _originals_cache[msg_id] = text

def _link_message(msg_id: int, text: str, saved_row_id: Optional[str]) -> None:
    """
    Кэш в памяти процесса (как раньше) + фоновая привязка telegram
    message_id к уже сохранённой строке в БД. In-memory кэш обнуляется
    при рестарте/редеплое — привязка в БД переживает его, и Text/
    Translate продолжают работать через db.get_message_by_telegram_id
    как фоллбэк (см. handle_translate/flow_translate/flow_show_text).
    """
    _cache_original(msg_id, text)
    if saved_row_id:
        asyncio.create_task(db.set_message_telegram_id(saved_row_id, msg_id))

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

def _cache_bubble_group(last_msg_id: int, prev_msg_ids: list[int]):
    if len(_bubble_group_cache) > 1000:
        _bubble_group_cache.clear()
    _bubble_group_cache[last_msg_id] = prev_msg_ids

_recast_cache: Dict[int, list] = {}  # msg_id → ["recast phrase 1", "recast phrase 2"]

def _cache_recast(msg_id: int, phrases: list):
    if len(_recast_cache) > 5000:
        _recast_cache.clear()
    _recast_cache[msg_id] = phrases

def _extract_recast_phrases(text: str) -> list:
    """Извлекает фразы обёрнутые в **bold** из текста LLM."""
    import re as _re
    return _re.findall(r'\*\*(.+?)\*\*', text)

async def _preload_translation(msg_id: int, clean_text: str, recast_phrases: list) -> None:
    """
    Фоновая задача: переводит текст и кладёт в _translation_cache.
    recast_phrases — список фраз которые нужно выделить bold в переводе.
    """
    try:
        translation = await groq_client.translate_text(
            clean_text, recast_phrases=recast_phrases
        )
        safe = _md_bold_to_html(html.escape(translation))
        _cache_translation(msg_id, safe)
    except Exception as e:
        logger.error(f"Error in _preload_translation for msg {msg_id}: {e}")

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

LEVEL_ORDER = ["beginner", "intermediate", "advanced"]

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

# ─── Лимит сообщений ─────────────────────────────────────────────────────────


async def _notify_expired(message) -> None:
    """
    Ленивый путь: подписка/триал истекли ровно на этом сообщении
    (check_message_limit -> check_subscription_expired обнаружил это
    прямо сейчас). Проактивный путь для тех, кто не пишет боту вообще —
    scheduler.send_expiry_notifications.
    """
    await message.answer(
        "⏳ <b>Your Pro access has ended</b>\n\n"
        "You're back on the free plan — Mrs. Smith is still here, "
        "10 messages a day. Upgrade anytime from /settings.",
        parse_mode="HTML"
    )


async def _show_limit_reached(message, limit_info: dict) -> None:
    """
    Показывает сообщение о достижении дневного лимита free-плана.
    Единственный тариф с конечным лимитом — free (Pro безлимитный),
    так что plan здесь всегда "free" — отдельная ветка для других
    тарифов больше не нужна с тех пор, как Standard убрали.
    """
    from src.bot.keyboards import get_paywall_period_keyboard
    limit = limit_info.get("limit", 10)
    text = (
        f"You've used all <b>{limit}</b> free messages today. 🎯\n\n"
        f"Upgrade to continue — or come back tomorrow."
    )
    await message.answer(text, parse_mode="HTML", reply_markup=get_paywall_period_keyboard())


# ─── Session Summary ──────────────────────────────────────────────────────────

@router.callback_query(F.data == "session_summary")
async def cq_session_summary(callback: CallbackQuery, state: FSMContext):
    """
    Генерирует и отправляет голосовое саммари сессии от Mrs. Smith —
    личное сообщение, не документ. Под голосовым — те же кнопки
    Text/Translate, что и у любого другого голосового ответа в приложении.
    """
    try:
        user_id = callback.from_user.id
        await callback.answer("Mrs. Smith is putting it together…")
        await safe_edit_reply_markup(callback.message, reply_markup=None)

        messages = await db.get_messages_for_summary(user_id, limit=50)
        if not messages:
            await callback.message.answer("No messages to summarize yet.")
            return

        summary_text = await groq_client.generate_session_voice_summary(messages)
        persona_display = get_persona_display("mrs_smith")

        voice_bytes = await groq_client.text_to_speech(summary_text, voice="diana")
        if voice_bytes:
            voice_file = BufferedInputFile(voice_bytes, filename="session_summary.wav")
            sent = await callback.message.answer_voice(
                voice_file,
                caption=f"{persona_display} — Session Summary",
                reply_markup=get_flow_voice_keyboard(0)
            )
            _cache_original(sent.message_id, summary_text)
            _cache_persona_display(sent.message_id, persona_display)
            await safe_edit_reply_markup(sent, reply_markup=get_flow_voice_keyboard(sent.message_id))
        else:
            safe_text = html.escape(summary_text)
            sent = await callback.message.answer(
                f"💬 {safe_text}\n\n<i>{persona_display}</i>",
                parse_mode="HTML"
            )
            _cache_original(sent.message_id, summary_text)
            _cache_persona_display(sent.message_id, persona_display)
            await safe_edit_reply_markup(sent, reply_markup=get_translate_keyboard(sent.message_id))

    except Exception as e:
        logger.error(f"Error in session_summary: {e}")
        await callback.message.answer("Something went wrong.")


@router.callback_query(F.data == "session_summary_skip")
async def cq_session_summary_skip(callback: CallbackQuery):
    """Юзер отказался от саммари при выходе из Tutor Mode."""
    try:
        await callback.answer()
        await safe_edit_reply_markup(callback.message, reply_markup=None)
    except Exception as e:
        logger.error(f"Error in session_summary_skip: {e}")


# ─── Mode activation ──────────────────────────────────────────────────────────


@router.message(F.text == "🎙 Flow")
async def activate_flow(message: Message, state: FSMContext):
    user = await db.get_or_create_user(message.from_user.id)
    plan = user.get("subscription_plan", "free")
    await state.update_data(active_mode=MODE_FLOW)
    await state.set_state(FlowState.choosing_persona)
    await message.answer("Who would you like to talk to?", reply_markup=get_persona_keyboard(plan))

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
        reply_markup=get_tutor_keyboard()
    )

@router.message(F.text == "⏹ Stop Tutor")
async def deactivate_tutor(message: Message, state: FSMContext):
    await state.clear()
    await message.answer("⏹ Tutor Mode off", reply_markup=get_mode_keyboard())

    user_id = message.from_user.id
    user = await db.get_or_create_user(user_id)
    plan = user.get("subscription_plan", "free")
    if has_feature(plan, "session_summary"):
        history = await db.get_messages_for_summary(user_id, limit=1)
        if history:
            await message.answer(
                "📚 Want a quick Session Summary from Mrs. Smith before you go?",
                reply_markup=get_session_summary_offer_keyboard()
            )

@router.message(F.text == "✉️ PenFriend")
async def activate_penfriend(message: Message, state: FSMContext):
    user = await db.get_or_create_user(message.from_user.id)
    plan = user.get("subscription_plan", "free")
    await db.update_mode(message.from_user.id, MODE_PENFRIEND)
    await state.update_data(active_mode=MODE_PENFRIEND)
    await state.set_state(FlowState.choosing_persona)
    await message.answer(
        "✉️ <b>PenFriend Mode</b>\n\nWho would you like to write to?",
        parse_mode="HTML",
        reply_markup=get_persona_keyboard(plan)
    )

@router.message(F.text == "⏹ Stop PenFriend")
async def deactivate_penfriend(message: Message, state: FSMContext):
    await state.clear()
    await message.answer("⏹ PenFriend Mode off", reply_markup=get_mode_keyboard())

# ─── Synonym Streak (active) ───────────────────────────────────────────────
# Ping-pong с Mrs. Smith: юзер называет слово → получает синонимы с примерами →
# пробует использовать один из них в своём предложении → мягкий фидбек.

@router.message(F.text == "🔄 Synonym Streak")
async def activate_synonym_streak(message: Message, state: FSMContext):
    try:
        user_id = message.from_user.id
        user = await db.get_or_create_user(user_id)
        plan = user.get("subscription_plan", "free")

        if not has_feature(plan, "synonym_streak"):
            await message.answer(
                "🔄 <b>Synonym Streak</b> is a Pro feature — Mrs. Smith helps you find "
                "and practice synonyms on demand.\n\nUpgrade to unlock it.",
                parse_mode="HTML",
                reply_markup=get_paywall_period_keyboard()
            )
            return

        await state.clear()
        await state.set_state(SynonymStreakState.awaiting_word)
        await message.answer(
            "📚 Which word would you like synonyms for? Just send it — "
            "or ask me like <i>\"help me with the word 'interesting'\"</i>.",
            parse_mode="HTML",
            reply_markup=get_synonym_streak_keyboard()
        )
    except Exception as e:
        logger.error(f"Error in activate_synonym_streak: {e}")
        await message.answer("Something went wrong.")

@router.message(F.text == "⏹ Stop Synonym Streak")
async def deactivate_synonym_streak(message: Message, state: FSMContext):
    await state.clear()
    await message.answer("⏹ Synonym Streak off", reply_markup=get_mode_keyboard())

@router.message(F.text == "🔄 New word")
async def synonym_streak_new_word(message: Message, state: FSMContext):
    await state.set_state(SynonymStreakState.awaiting_word)
    await message.answer("📚 Which word next?", reply_markup=get_synonym_streak_keyboard())

@router.message(SynonymStreakState.awaiting_word)
async def synonym_streak_word(message: Message, state: FSMContext, user: Dict[str, Any] = None):
    """Юзер назвал слово (текстом или голосом) — Mrs. Smith даёт синонимы с примерами."""
    try:
        user_id = message.from_user.id
        if user is None:
            user = await db.get_or_create_user(user_id)

        if message.voice:
            await message.bot.send_chat_action(user_id, "record_voice")
            voice_file = await message.bot.get_file(message.voice.file_id)
            voice_bytes = await message.bot.download_file(voice_file.file_path)
            user_text = await transcribe_voice_with_groq(voice_bytes.read())
            if not user_text or user_text.startswith("[Transcription error"):
                await message.answer("Could not transcribe your voice message. Please try again.")
                return
        elif message.text:
            user_text = message.text.strip()
            if not user_text or user_text.startswith("/") or user_text in BUTTON_TEXTS:
                return
        else:
            await message.answer(
                "Send me the word as text or voice — for example: "
                "\"help me with the word 'interesting'\"."
            )
            return

        await message.bot.send_chat_action(user_id, "typing")
        level = user.get("level", settings.DEFAULT_USER_LEVEL)
        lesson = await groq_client.generate_synonym_lesson(user_text, level)

        word = (lesson.get("word") or "").strip()
        synonyms = lesson.get("synonyms") or []

        if not word or not synonyms:
            await message.answer(
                "Hmm, I'm not sure which word you meant. Try something like: "
                "\"help me with the word 'interesting'\"."
            )
            return

        intro = (lesson.get("intro") or "").strip()
        lines = [html.escape(intro)] if intro else []
        for item in synonyms[:3]:
            syn = html.escape(str(item.get("synonym", "")).strip())
            example = html.escape(str(item.get("example", "")).strip())
            if not syn:
                continue
            lines.append(f"• <b>{syn}</b> — {example}" if example else f"• <b>{syn}</b>")
        lines.append(f"\nNow try using one of these for <b>{html.escape(word)}</b> in your own sentence.")

        await message.answer(
            "📚 " + "\n".join(lines),
            parse_mode="HTML",
            reply_markup=get_synonym_streak_keyboard()
        )

        await state.update_data(
            synonym_word=word,
            synonym_options=[str(s.get("synonym", "")).strip() for s in synonyms if s.get("synonym")],
        )
        await state.set_state(SynonymStreakState.awaiting_attempt)

    except Exception as e:
        logger.error(f"Error in synonym_streak_word: {e}")
        await message.answer("Something went wrong. Try again?")

@router.message(SynonymStreakState.awaiting_attempt)
async def synonym_streak_attempt(message: Message, state: FSMContext, user: Dict[str, Any] = None):
    """Юзер попробовал использовать синоним — мягкая проверка + фидбек от Mrs. Smith."""
    try:
        user_id = message.from_user.id
        if user is None:
            user = await db.get_or_create_user(user_id)

        if message.voice:
            await message.bot.send_chat_action(user_id, "record_voice")
            voice_file = await message.bot.get_file(message.voice.file_id)
            voice_bytes = await message.bot.download_file(voice_file.file_path)
            attempt_text = await transcribe_voice_with_groq(voice_bytes.read())
            if not attempt_text or attempt_text.startswith("[Transcription error"):
                await message.answer("Could not transcribe your voice message. Please try again.")
                return
        elif message.text:
            attempt_text = message.text.strip()
            if not attempt_text or attempt_text.startswith("/") or attempt_text in BUTTON_TEXTS:
                return
        else:
            return

        data = await state.get_data()
        word = data.get("synonym_word", "")
        synonyms = data.get("synonym_options", [])

        await message.bot.send_chat_action(user_id, "typing")
        level = user.get("level", settings.DEFAULT_USER_LEVEL)
        result = await groq_client.evaluate_synonym_attempt(word, synonyms, attempt_text, level)

        feedback = html.escape((result.get("feedback") or "").strip()) or "Nice try — want to give it another go?"
        await message.answer(
            f"📚 {feedback}",
            parse_mode="HTML",
            reply_markup=get_synonym_streak_keyboard()
        )

        # Готовы к следующему слову без повторного нажатия кнопки
        await state.set_state(SynonymStreakState.awaiting_word)

    except Exception as e:
        logger.error(f"Error in synonym_streak_attempt: {e}")
        await message.answer("Something went wrong. Try again?")

@router.message(F.text == "↩ Switch")
async def flow_switch_reply(message: Message, state: FSMContext):
    try:
        user_id = message.from_user.id
        fsm_data = await state.get_data()
        current_mode = fsm_data.get("active_mode", MODE_FLOW)

        user = await db.get_or_create_user(user_id)
        plan = user.get("subscription_plan", "free")

        history = await db.get_history(user_id, limit=settings.CONTEXT_WINDOW)
        context_text = " | ".join([
            f"{m['role']}: {m['content']}" for m in history
        ]) if history else ""

        await state.update_data(switch_context=context_text, active_mode=current_mode)
        await state.set_state(FlowState.choosing_persona)
        await message.answer("Who would you like to talk to next?", reply_markup=get_persona_keyboard(plan))
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

        user_for_plan = await db.get_or_create_user(user_id)
        plan = user_for_plan.get("subscription_plan", "free")
        if persona_key not in get_available_personas(plan):
            await callback.answer()
            await callback.message.answer(
                f"🔒 {get_persona_display(persona_key)} is a Pro feature.\n\n"
                f"Upgrade to unlock all 6 characters.",
                parse_mode="HTML",
                reply_markup=get_paywall_period_keyboard()
            )
            return

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
            settings_plan = user.get("subscription_plan") or "free"
            await safe_edit_text(
                callback.message,
                f"👤 Now talking to {display_name}.\n\n⚙️ <b>Settings</b>",
                parse_mode="HTML",
                reply_markup=get_settings_keyboard(notif, recasting, practice, user_id, settings_plan)
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

        greeting_row_id = await db.save_message(user_id, "assistant", greeting)
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
                _link_message(sent.message_id, greeting, greeting_row_id)
                _cache_persona_display(sent.message_id, persona_display)
                await safe_edit_reply_markup(sent, reply_markup=get_flow_voice_keyboard(sent.message_id))
            else:
                # Fallback — текст, если TTS не сработал
                safe_greeting = html.escape(greeting)
                sent = await callback.message.answer(f"💬 {safe_greeting}\n\n<i>{persona_display}</i>", parse_mode="HTML")
                _link_message(sent.message_id, greeting, greeting_row_id)
                _cache_persona_display(sent.message_id, persona_display)
                await safe_edit_reply_markup(sent, reply_markup=get_translate_keyboard(sent.message_id))
        else:
            safe_greeting = html.escape(greeting)
            sent = await callback.message.answer(f"💬 {safe_greeting}\n\n<i>{persona_display}</i>", parse_mode="HTML")
            _link_message(sent.message_id, greeting, greeting_row_id)
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

        # Персонаж мог стать недоступен уже ПОСЛЕ выбора (например, закончился
        # триал) — flow_persona_selected проверяет план только в момент
        # переключения, здесь перепроверяем на каждое сообщение, иначе
        # разговор с запертым персонажем продолжался бы бесконечно.
        plan = user.get("subscription_plan") or "free"
        if persona_key not in get_available_personas(plan):
            await message.answer(
                f"🔒 Your access to {get_persona_display(persona_key)} has ended — "
                f"this character is part of Pro.\n\n"
                f"Upgrade to keep talking to them, or press ↩ Switch to pick an available character.",
                parse_mode="HTML",
                reply_markup=get_paywall_period_keyboard()
            )
            return

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

        # ─── Проверка лимита сообщений ────────────────────────────────────────
        if user_id not in ADMIN_IDS:
            limit_info = await db.check_message_limit(user_id)
            if limit_info.get("just_expired"):
                await _notify_expired(message)
            if not limit_info["allowed"]:
                await _show_limit_reached(message, limit_info)
                return
            await db.increment_daily_messages(user_id)

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

        chat_response_row_id = await db.save_message(user_id, "assistant", chat_response)
        persona_display = get_persona_display(persona_key)

        if active_mode == MODE_PENFRIEND:
            # Мультибабл: последовательная отправка с typing-задержками
            # Translate только под последним сообщением — переводит ВСЕ баблы этого ответа

            # Извлекаем recast маркер из первого бабла если есть
            # Формат: __RECAST__correct_word__RECAST__message text
            all_recast_phrases = []
            clean_bubbles = []
            for b in pf_bubbles:
                if b.startswith("__RECAST__"):
                    parts = b.split("__RECAST__", 2)
                    # parts[0] = "", parts[1] = correct_word, parts[2] = message
                    correct_word = parts[1].strip()
                    message_text = parts[2] if len(parts) > 2 else ""
                    if correct_word:
                        all_recast_phrases.append(correct_word)
                    clean_bubbles.append(message_text)
                else:
                    clean_bubbles.append(b)
            pf_bubbles = clean_bubbles

            # Выделяем correct_word bold в тексте баблов
            if all_recast_phrases:
                import re as _re
                bolded_bubbles = []
                recast_done = False
                for b in pf_bubbles:
                    if not recast_done:
                        for phrase in all_recast_phrases:
                            # case-insensitive поиск, выделяем первое вхождение
                            pattern = re.compile(re.escape(phrase), re.IGNORECASE)
                            new_b = pattern.sub(f"**{phrase}**", b, count=1)
                            if new_b != b:
                                b = new_b
                                recast_done = True
                                break
                    bolded_bubbles.append(b)
                pf_bubbles = bolded_bubbles

            # Готовим объединённый текст всех баблов для кэша
            all_bubbles_display = "\n\n".join(
                _md_bold_to_html(html.escape(b)) for b in pf_bubbles
            )
            # Чистый текст без ** для переводчика
            all_bubbles_clean = "\n\n".join(
                re.sub(r'\*\*(.+?)\*\*', r'\1', b) for b in pf_bubbles
            )

            # Переводим все баблы заранее — спойлер добавляется под последним
            translation_html = ""
            try:
                translation_raw = await groq_client.translate_text(
                    all_bubbles_clean, recast_phrases=all_recast_phrases
                )
                translation_html = _md_bold_to_html(html.escape(translation_raw))
            except Exception as e:
                logger.error(f"Error preloading translation: {e}")

            for i, bubble in enumerate(pf_bubbles):
                is_last = (i == len(pf_bubbles) - 1)
                delay = _penfriend_typing_delay(bubble)
                await message.bot.send_chat_action(user_id, "typing")
                await asyncio.sleep(delay)
                safe_bubble = html.escape(bubble)
                display_bubble = _md_bold_to_html(safe_bubble)
                if is_last:
                    # Спойлер с переводом под последним баблом
                    spoiler = ""
                    if translation_html:
                        spoiler = f"\n\n<blockquote expandable>🌐 {translation_html}</blockquote>"
                    sent = await message.answer(
                        f"💬 {display_bubble}\n\n<i>{persona_display}</i>{spoiler}",
                        parse_mode="HTML"
                    )
                else:
                    sent = await message.answer(f"💬 {display_bubble}", parse_mode="HTML")
        else:
            voice_bytes = await groq_client.text_to_speech(chat_response, voice=voice)
            if voice_bytes:
                voice_file = BufferedInputFile(voice_bytes, filename="response.wav")
                sent = await message.answer_voice(
                    voice_file,
                    caption=persona_display,
                    reply_markup=get_flow_voice_keyboard(0)
                )
                _link_message(sent.message_id, chat_response, chat_response_row_id)
                _cache_persona_display(sent.message_id, persona_display)
                await safe_edit_reply_markup(sent, reply_markup=get_flow_voice_keyboard(sent.message_id))
            else:
                # Fallback — текст, если TTS не сработал (Flow голосовой, но не оставлять юзера без ответа)
                safe_response = html.escape(chat_response)
                display_response = _md_bold_to_html(safe_response)
                sent = await message.answer(
                    f"💬 {display_response}\n\n<i>{html.escape(persona_display)}</i>",
                    parse_mode="HTML"
                )
                _link_message(sent.message_id, chat_response, chat_response_row_id)
                _cache_display(sent.message_id, display_response)
                _cache_persona_display(sent.message_id, persona_display)
                await safe_edit_reply_markup(sent, reply_markup=get_translate_keyboard(sent.message_id))

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

        # ─── Проверка лимита сообщений ────────────────────────────────────────
        if not is_admin:
            limit_info = await db.check_message_limit(user_id)
            if limit_info.get("just_expired"):
                await _notify_expired(message)
            if not limit_info["allowed"]:
                await _show_limit_reached(message, limit_info)
                return
            await db.increment_daily_messages(user_id)

        sent_user_msg_id = None
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
            sent_user_msg_id = sent_user.message_id
            _cache_original(sent_user_msg_id, user_text)
            await safe_edit_reply_markup(
                sent_user,
                reply_markup=get_flow_user_voice_keyboard(sent_user_msg_id)
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
        user_text_row_id = await db.save_message(user_id, "user", user_text)
        if sent_user_msg_id and user_text_row_id:
            asyncio.create_task(db.set_message_telegram_id(user_text_row_id, sent_user_msg_id))

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

        chat_response_row_id = await db.save_message(user_id, "assistant", chat_response)
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

        # Карточка ошибки только в Tutor Mode — в PenFriend коррекция через recasting
        user_mode = user.get("mode", MODE_TUTOR)
        if has_real_error and user_mode != MODE_PENFRIEND:
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

        persona_display = get_persona_display(user.get("persona", "mrs_smith"))
        voice_sent = False
        if should_reply_voice:
            await message.bot.send_chat_action(user_id, "record_voice")
            voice_bytes_out = await groq_client.text_to_speech(chat_response, voice=voice)
            if voice_bytes_out:
                voice_file_out = BufferedInputFile(voice_bytes_out, filename="response.wav")
                sent_voice = await message.answer_voice(
                    voice_file_out,
                    caption=persona_display,
                    reply_markup=get_flow_voice_keyboard(0)
                )
                _link_message(sent_voice.message_id, chat_response, chat_response_row_id)
                await safe_edit_reply_markup(
                    sent_voice,
                    reply_markup=get_flow_voice_keyboard(sent_voice.message_id)
                )
                voice_sent = True

        if not voice_sent:
            # Либо голос выключен настройкой, либо TTS не сработал — в обоих
            # случаях юзер должен получить хоть какой-то ответ текстом.
            safe_response = html.escape(chat_response)
            display_response = _md_bold_to_html(safe_response)
            sent = await message.answer(f"💬 {display_response}\n\n<i>{html.escape(persona_display)}</i>", parse_mode="HTML")
            _link_message(sent.message_id, chat_response, chat_response_row_id)
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

async def _get_original_text(user_id: int, message_id: int) -> Optional[str]:
    """
    Text/Translate/Original кнопки: сначала in-memory кэш (быстро), затем
    фоллбэк на БД по telegram_message_id — переживает рестарт процесса,
    когда _originals_cache уже пуст. При попадании в БД догревает кэш.
    """
    text = _originals_cache.get(message_id)
    if text:
        return text
    text = await db.get_message_by_telegram_id(user_id, message_id)
    if text:
        _cache_original(message_id, text)
    return text

@router.callback_query(F.data.startswith("translate_"))
async def handle_translate(callback: CallbackQuery):
    try:
        message_id = int(callback.data.split("_")[1])
        original_text = await _get_original_text(callback.from_user.id, message_id)

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

        # Удаляем предыдущие баблы этого ответа (если мультибабл)
        prev_ids = _bubble_group_cache.get(message_id, [])
        for prev_id in prev_ids:
            try:
                await callback.message.bot.delete_message(callback.message.chat.id, prev_id)
            except Exception:
                pass  # уже удалено или недоступно

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
        original_text = await _get_original_text(callback.from_user.id, message_id)

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
        user_text = await _get_original_text(callback.from_user.id, message_id)
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
        original = await _get_original_text(callback.from_user.id, message_id)
        if not original:
            await callback.answer("Text not available.", show_alert=True)
            return
        safe_text = html.escape(original)
        persona_disp = _persona_display_cache.get(message_id, "")
        caption_text = f"💬 {safe_text}\n\n<i>{html.escape(persona_disp)}</i>" if persona_disp else f"💬 {safe_text}"
        if len(caption_text) > TELEGRAM_CAPTION_LIMIT:
            # Подпись к голосовому ограничена 1024 символами (в отличие от
            # обычного текстового сообщения) — длинные тексты (например,
            # Session Summary) в неё не влезают и Telegram отвечает
            # MEDIA_CAPTION_TOO_LONG. Показываем текст отдельным сообщением
            # с обычной текстовой клавиатурой Translate вместо правки подписи.
            sent = await callback.message.answer(caption_text, parse_mode="HTML")
            _cache_original(sent.message_id, original)
            _cache_persona_display(sent.message_id, persona_disp)
            await safe_edit_reply_markup(sent, reply_markup=get_translate_keyboard(sent.message_id))
        else:
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
        original = await _get_original_text(callback.from_user.id, message_id)
        if not original:
            await callback.answer("Text not available.", show_alert=True)
            return
        translation = await groq_client.translate_text(original)
        safe_translation = html.escape(translation)
        caption_text = f"🌐 {safe_translation}"
        if len(caption_text) > TELEGRAM_CAPTION_LIMIT:
            # См. flow_show_text — перевод длинного текста (например,
            # Session Summary) тоже не влезает в лимит подписи к медиа.
            sent = await callback.message.answer(caption_text, parse_mode="HTML")
            _cache_original(sent.message_id, original)
            await safe_edit_reply_markup(sent, reply_markup=get_original_keyboard(sent.message_id))
        else:
            await safe_edit_caption(
                callback.message,
                caption=caption_text,
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
        original = await _get_original_text(callback.from_user.id, message_id)
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
        user_text = await _get_original_text(callback.from_user.id, message_id)
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
        user_text = await _get_original_text(callback.from_user.id, message_id)
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
