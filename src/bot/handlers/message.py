import logging
import html
import asyncio
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
from src.personas import (
    get_persona_voice, get_persona_display, get_persona_tutor_prompt,
    get_voice_excuse, get_last_exchange_instruction
)
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
logger = logging.getLogger(__name__)

import time as _time

# Кеш оригинальных текстов для кнопки Original: {message_id: (text, timestamp)}
# TTL = 2 часа — сообщения старше этого времени из кэша вычищаются
_CACHE_TTL = 7200  # секунд

class _TTLCache:
    def __init__(self, ttl: int):
        self._ttl = ttl
        self._data: Dict[int, tuple] = {}  # {key: (value, timestamp)}

    def set(self, key: int, value: str) -> None:
        self._data[key] = (value, _time.monotonic())
        self._maybe_evict()

    def get(self, key: int) -> str | None:
        entry = self._data.get(key)
        if entry is None:
            return None
        value, ts = entry
        if _time.monotonic() - ts > self._ttl:
            del self._data[key]
            return None
        return value

    def _maybe_evict(self) -> None:
        """Вычищаем протухшие записи раз в ~100 новых записей"""
        if len(self._data) % 100 == 0:
            now = _time.monotonic()
            expired = [k for k, (_, ts) in self._data.items() if now - ts > self._ttl]
            for k in expired:
                del self._data[k]

_originals_cache = _TTLCache(_CACHE_TTL)

# Порог схлопывания саммари
SUMMARY_MERGE_THRESHOLD = 4



# ─── PenFriend typing delay ────────────────────────────────────────────────

def _penfriend_typing_delay(text: str) -> float:
    """
    Имитирует время набора текста живым человеком.
    ~55 слов/мин — спокойный темп. Диапазон: 2–9 секунд.
    """
    words = len(text.split())
    seconds = (words / 55) * 60 * 0.7
    return max(1.4, min(seconds, 6.3))



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
    _originals_cache.set(sent.message_id, chat_response)

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

@router.message(F.text == "🎙 Flow")
async def activate_flow(message: Message, state: FSMContext):
    await db.update_mode(message.from_user.id, MODE_FLOW)
    await state.set_state(FlowState.choosing_persona)
    await state.update_data(active_mode=MODE_FLOW)
    await message.answer(
        "Who would you like to talk to?",
        reply_markup=get_persona_keyboard()
    )


@router.message(F.text == "⏹ Stop Flow")
async def deactivate_flow(message: Message, state: FSMContext):
    await state.clear()
    await message.answer("⏹ Flow Mode off", reply_markup=get_mode_keyboard())


# ─── Tutor Mode ────────────────────────────────────────────────────────────

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


# ─── PenFriend Mode ────────────────────────────────────────────────────────

@router.message(F.text == "✉️ PenFriend")
async def activate_penfriend(message: Message, state: FSMContext):
    await db.update_mode(message.from_user.id, MODE_PENFRIEND)
    await state.set_state(FlowState.choosing_persona)
    await state.update_data(active_mode=MODE_PENFRIEND)
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
    """Switch через Reply-кнопку"""
    try:
        user_id = message.from_user.id
        history = await db.get_history(user_id, limit=settings.CONTEXT_WINDOW)
        context_text = " | ".join([
            f"{m['role']}: {m['content']}" for m in history
        ]) if history else ""

        await state.update_data(switch_context=context_text)
        await state.set_state(FlowState.choosing_persona)

        await message.answer(
            "Who would you like to talk to next?",
            reply_markup=get_persona_keyboard()
        )
    except Exception as e:
        logger.error(f"Error in flow switch reply: {e}")
        await message.answer("Something went wrong. Try again.")


# ─── Flow Mode: выбор персонажа ────────────────────────────────────────────

@router.callback_query(F.data.startswith("persona_"), FlowState.choosing_persona)
async def flow_persona_selected(callback: CallbackQuery, state: FSMContext):
    try:
        persona_key = callback.data.split("_", 1)[1]
        user_id = callback.from_user.id
        voice = get_persona_voice(persona_key)

        fsm_data = await state.get_data()
        switch_context = fsm_data.get("switch_context", "")
        active_mode = fsm_data.get("active_mode", MODE_FLOW)
        user = await db.get_or_create_user(user_id)

        await db.update_user_persona(user_id, persona_key)
        await db.update_user_voice(user_id, voice)

        await state.update_data(
            persona_key=persona_key,
            voice=voice,
            switch_context="",
            active_mode=active_mode
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

        # Уведомление о смене персонажа
        persona_display = get_persona_display(persona_key)
        if switch_context:
            await callback.message.answer(f"↩ Switched to {persona_display}", parse_mode="HTML")
        elif active_mode == MODE_PENFRIEND:
            await callback.message.answer(f"✉️ PenFriend Mode — {persona_display}", parse_mode="HTML", reply_markup=get_penfriend_keyboard())
        else:
            await callback.message.answer(f"🎙 Flow Mode — {persona_display}", parse_mode="HTML", reply_markup=get_flow_stop_keyboard())

        # Приветствие — текст для PenFriend, голос для Flow
        if active_mode == MODE_PENFRIEND:
            safe_greeting = html.escape(greeting)
            sent = await callback.message.answer(
                f"💬 {safe_greeting}",
                parse_mode="HTML",
                reply_markup=get_translate_keyboard(0)
            )
            _originals_cache.set(sent.message_id, greeting)
            await sent.edit_reply_markup(reply_markup=get_translate_keyboard(sent.message_id))
        else:
            # Flow — проверяем голосовой триал (админ пропускает)
            trial = {"exhausted": False} if user_id in ADMIN_IDS else await db.get_voice_trial(user_id)
            if trial["exhausted"]:
                # Триал исчерпан — персонаж объясняет в своей вселенной
                excuse = get_voice_excuse(persona_key)
                sent = await callback.message.answer(
                    f"💬 {html.escape(excuse)}",
                    parse_mode="HTML",
                    reply_markup=get_translate_keyboard(0)
                )
                _originals_cache.set(sent.message_id, excuse)
                await sent.edit_reply_markup(reply_markup=get_translate_keyboard(sent.message_id))
            else:
                await db.increment_voice_trial(user_id)
                voice_bytes = await groq_client.text_to_speech(greeting, voice=voice)
                if voice_bytes:
                    voice_file = BufferedInputFile(voice_bytes, filename="greeting.wav")
                    sent = await callback.message.answer_voice(
                        voice_file,
                        caption=persona_display,
                        reply_markup=get_flow_voice_keyboard(0)
                    )
                    _originals_cache.set(sent.message_id, greeting)
                    await sent.edit_reply_markup(
                        reply_markup=get_flow_voice_keyboard(sent.message_id)
                    )

        await callback.answer()

    except Exception as e:
        logger.error(f"Error in flow persona selection: {e}")
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
        active_mode = fsm_data.get("active_mode", MODE_FLOW)

        # ─── Защита PenFriend от голосовых ────────────────────────────────
        if active_mode == MODE_PENFRIEND and message.voice:
            await message.answer(
                "✉️ PenFriend is text-only.\n"
                "Try typing what you wanted to say — it is good writing practice too."
            )
            return

        if message.voice:
            # Проверяем триал перед транскрипцией (админ пропускает)
            trial = {"exhausted": False} if user_id in ADMIN_IDS else await db.get_voice_trial(user_id)
            if trial["exhausted"]:
                # Не транскрибируем — персонаж сразу объясняет в своей вселенной
                excuse = get_voice_excuse(persona_key)
                sent = await message.answer(
                    f"💬 {html.escape(excuse)}",
                    parse_mode="HTML",
                    reply_markup=get_translate_keyboard(0)
                )
                _originals_cache.set(sent.message_id, excuse)
                await sent.edit_reply_markup(reply_markup=get_translate_keyboard(sent.message_id))
                return
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
        errors_task = asyncio.create_task(db.get_top_error_categories(user_id, limit=2))

        history, summary, is_farewell, top_errors = await asyncio.gather(
            history_task, summary_task, farewell_task, errors_task
        )

        # Определяем последний голосовой обмен — персонаж добавит прощание с голосом
        trial_for_last = (
            {"used": 0, "exhausted": False}
            if user_id in ADMIN_IDS
            else await db.get_voice_trial(user_id)
        )
        is_last_voice_exchange = (
            active_mode not in (MODE_PENFRIEND,)
            and not trial_for_last["exhausted"]
            and trial_for_last["used"] + 1 >= db.FREE_VOICE_EXCHANGES
        )
        last_exchange_note = (
            get_last_exchange_instruction(persona_key)
            if is_last_voice_exchange else ""
        )

        await message.bot.send_chat_action(user_id, "record_voice")
        chat_response = await groq_client.generate_flow_response(
            text=user_text,
            persona_key=persona_key,
            history=history,
            summary=summary,
            session_count=user.get("session_count", 0),
            top_errors=top_errors,
            extra_instruction=last_exchange_note
        )
        await db.save_message(user_id, "assistant", chat_response)

        persona_display = get_persona_display(persona_key)

        if active_mode == MODE_PENFRIEND:
            # PenFriend — только текст с имитацией набора
            delay = _penfriend_typing_delay(chat_response)
            await message.bot.send_chat_action(user_id, "typing")
            await asyncio.sleep(delay)
            safe_response = html.escape(chat_response)
            sent = await message.answer(f"💬 {safe_response}", parse_mode="HTML")
            _originals_cache.set(sent.message_id, chat_response)
            await sent.edit_reply_markup(reply_markup=get_translate_keyboard(sent.message_id))
        else:
            # Flow — проверяем триал для голоса персонажа (админ пропускает)
            trial = {"exhausted": False} if user_id in ADMIN_IDS else await db.get_voice_trial(user_id)
            if trial["exhausted"]:
                # Триал исчерпан — персонаж объясняет в своей вселенной, голос не нужен
                excuse = get_voice_excuse(persona_key)
                sent = await message.answer(
                    f"💬 {html.escape(excuse)}",
                    parse_mode="HTML",
                    reply_markup=get_translate_keyboard(0)
                )
                _originals_cache.set(sent.message_id, excuse)
                await sent.edit_reply_markup(reply_markup=get_translate_keyboard(sent.message_id))
            else:
                await db.increment_voice_trial(user_id)
                voice_bytes = await groq_client.text_to_speech(chat_response, voice=voice)
                if voice_bytes:
                    voice_file = BufferedInputFile(voice_bytes, filename="response.wav")
                    sent = await message.answer_voice(
                        voice_file,
                        caption=persona_display,
                        reply_markup=get_flow_voice_keyboard(0)
                    )
                    _originals_cache.set(sent.message_id, chat_response)
                    await sent.edit_reply_markup(
                        reply_markup=get_flow_voice_keyboard(sent.message_id)
                    )

        # Если прощание — саммаризация в фоне + инкремент сессии
        if is_farewell:
            asyncio.create_task(run_summarization(user_id))
            asyncio.create_task(db.increment_session_count(user_id))
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
            BUTTON_TEXTS = {"🎓 Tutor", "✉️ PenFriend", "🎙 Flow", "⏹ Stop Flow", "⏹ Stop Tutor", "⏹ Stop PenFriend", "↩ Switch"}
            if not user_text or user_text.startswith("/") or user_text in BUTTON_TEXTS:
                return
        else:
            return

        await db.save_message(user_id, "user", user_text)

        farewell_task = asyncio.create_task(groq_client.detect_farewell(user_text))
        history_task = asyncio.create_task(db.get_history(user_id, limit=settings.CONTEXT_WINDOW))
        summary_task = asyncio.create_task(db.get_latest_summary(user_id))
        history, is_farewell, summary = await asyncio.gather(history_task, farewell_task, summary_task)

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
            history=history,
            summary=summary
        )

        await db.save_message(user_id, "assistant", chat_response)

        if analysis_data.get('vocabulary_items'):
            for item in analysis_data['vocabulary_items']:
                await db.add_to_vocabulary(user_id, item)

        # Mastery check
        used_word = await db.find_word_in_text(user_id, user_text)
        if used_word:
            new_score = await db.increase_mastery(used_word["id"])
            if new_score >= 5:
                safe_word = html.escape(used_word["word_or_phrase"])
                await message.answer(
                    f"✅ <b>{safe_word}</b> — mastered! Added to your collection.",
                    parse_mode="HTML"
                )

        # Vocab reminder каждые 4 сообщения
        remind_counter = await db.increment_vocab_remind_counter(user_id)
        if remind_counter >= 4:
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

        # Блок коррекции — всегда показываем
        safe_corrected = html.escape(analysis_data.get('corrected_sentence', user_text))
        safe_explanation = html.escape(analysis_data.get('explanation', ''))
        analysis_text = f"✅ <b>Correct</b>\n{safe_corrected}\n\n💡 <b>Why</b>\n{safe_explanation}"
        if analysis_data.get('vocabulary_items'):
            analysis_text += "\n\n📚 <i>New words added to your vocabulary</i>"

        await message.answer(analysis_text, parse_mode="HTML")

        if not chat_response:
            chat_response = "Sorry, I couldn't formulate a response."

        voice = user.get("voice") or settings.TTS_VOICE

        # Голосовой ответ если нужен — без кнопок, просто аудио
        if should_reply_voice:
            voice_bytes_out = await groq_client.text_to_speech(chat_response, voice=voice)
            if voice_bytes_out:
                voice_file_out = BufferedInputFile(voice_bytes_out, filename="response.wav")
                await message.answer_voice(voice_file_out)

        # Текстовый ответ с кнопкой Translate
        safe_response = html.escape(chat_response)
        sent = await message.answer(f"💬 {safe_response}", parse_mode="HTML")
        _originals_cache.set(sent.message_id, chat_response)
        await sent.edit_reply_markup(reply_markup=get_translate_keyboard(sent.message_id))

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


# ─── Flow Mode: Text / Translate кнопки под голосовым ─────────────────────

@router.callback_query(F.data.startswith("flow_text_"))
async def flow_show_text(callback: CallbackQuery):
    """Показывает текст голосового ответа под самим голосовым"""
    try:
        message_id = int(callback.data.split("_")[2])
        original = _originals_cache.get(message_id)

        if not original:
            await callback.answer("Text not available.", show_alert=True)
            return

        safe_text = html.escape(original)
        await callback.message.reply(
            f"💬 {safe_text}",
            parse_mode="HTML",
            reply_markup=get_flow_voice_text_keyboard(message_id)
        )
        await callback.answer()
    except Exception as e:
        logger.error(f"Error in flow_text callback: {e}")
        await callback.answer("Error.", show_alert=True)


@router.callback_query(F.data.startswith("flow_translate_"))
async def flow_translate(callback: CallbackQuery):
    """Переводит текст голосового ответа"""
    try:
        message_id = int(callback.data.split("_")[2])
        original = _originals_cache.get(message_id)

        if not original:
            await callback.answer("Text not available.", show_alert=True)
            return

        translation = await groq_client.translate_text(original)
        safe_translation = html.escape(translation)

        await callback.message.reply(
            f"🌐 {safe_translation}",
            parse_mode="HTML",
            reply_markup=get_flow_voice_translate_keyboard(message_id)
        )
        await callback.answer()
    except Exception as e:
        logger.error(f"Error in flow_translate callback: {e}")
        await callback.answer("Translation failed.", show_alert=True)


@router.callback_query(F.data.startswith("flow_original_"))
async def flow_original(callback: CallbackQuery):
    """Возвращает оригинальный текст после перевода"""
    try:
        message_id = int(callback.data.split("_")[2])
        original = _originals_cache.get(message_id)

        if not original:
            await callback.answer("Text not available.", show_alert=True)
            return

        safe_text = html.escape(original)
        await callback.message.edit_text(
            f"💬 {safe_text}",
            parse_mode="HTML",
            reply_markup=get_flow_voice_text_keyboard(message_id)
        )
        await callback.answer()
    except Exception as e:
        logger.error(f"Error in flow_original callback: {e}")
        await callback.answer("Error.", show_alert=True)


# ─── Flow Mode: кнопки под голосовым ПОЛЬЗОВАТЕЛЯ ─────────────────────────

@router.callback_query(F.data.startswith("uvoice_text_"))
async def uvoice_show_text(callback: CallbackQuery):
    """Text + коррекция под голосовым пользователя в Flow Mode"""
    try:
        message_id = int(callback.data.split("_")[2])
        user_text = _originals_cache.get(message_id)

        if not user_text:
            await callback.answer("Text not available.", show_alert=True)
            return

        user = await db.get_or_create_user(callback.from_user.id)
        user_level = user.get("level", settings.DEFAULT_USER_LEVEL)

        # Генерируем коррекцию
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
    """Translate под голосовым пользователя в Flow Mode"""
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
