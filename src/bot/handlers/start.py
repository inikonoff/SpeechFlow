# CHANGELOG: 2026-07-16
# - Онбординг v2: шаги waiting_name → confirming_english_name → choosing_goal → waiting_level → choosing_mode
# - LLM предлагает английский вариант имени с объяснением
# - Выбор цели обучения через inline-кнопки
# - Имя и цель сохраняются в users, персонажи обращаются по имени

import logging
import html
from aiogram import Router, F
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.types import Message, CallbackQuery, BufferedInputFile
from aiogram.utils.keyboard import InlineKeyboardBuilder
from aiogram.types import InlineKeyboardButton

from src.bot.handlers.states import OnboardingState
from src.bot.keyboards import get_mode_keyboard, get_onboarding_mode_keyboard
from src.config import (
    ADMIN_IDS,
    ONBOARDING_VOICE_START,
    ONBOARDING_VOICE_BEGINNER,
    ONBOARDING_VOICE_INTERMEDIATE,
    ONBOARDING_VOICE_ADVANCED,
    ONBOARDING_SPOILERS,
)
from src.services.supabase_db import db

router = Router()
logger = logging.getLogger(__name__)

# ─── Цели обучения ────────────────────────────────────────────────────────────

LEARNING_GOALS = [
    ("🎯 Speak confidently at work",   "work"),
    ("✈️ Travel with ease",             "travel"),
    ("💬 Connect with people",          "connect"),
    ("🚀 Expand my skills",             "skills"),
    ("🌍 Live comfortably abroad",      "abroad"),
    ("📚 Just improve my English",      "general"),
]

# ─── Клавиатуры ───────────────────────────────────────────────────────────────

def get_onboarding_level_keyboard():
    builder = InlineKeyboardBuilder()
    builder.row(
        InlineKeyboardButton(text="Beginner",     callback_data="onb_level_beginner"),
        InlineKeyboardButton(text="Intermediate", callback_data="onb_level_intermediate"),
    )
    builder.row(
        InlineKeyboardButton(text="Advanced",     callback_data="onb_level_advanced"),
    )
    return builder.as_markup()

def get_learning_goal_keyboard():
    builder = InlineKeyboardBuilder()
    for label, key in LEARNING_GOALS:
        builder.row(InlineKeyboardButton(text=label, callback_data=f"onb_goal_{key}"))
    return builder.as_markup()

def get_english_name_keyboard(english_name: str):
    builder = InlineKeyboardBuilder()
    builder.row(
        InlineKeyboardButton(text=f"✅ Yes, call me {english_name}", callback_data="onb_name_accept"),
        InlineKeyboardButton(text="❌ Keep my name",                  callback_data="onb_name_keep"),
    )
    return builder.as_markup()

# ─── Хелперы ──────────────────────────────────────────────────────────────────

def _make_spoiler(key: str) -> str:
    data = ONBOARDING_SPOILERS.get(key, {})
    en = html.escape(data.get("en", ""))
    ru = html.escape(data.get("ru", ""))
    return f"<blockquote expandable>{en}\n\n{ru}</blockquote>"

async def _send_onboarding_voice(message: Message, file_id: str, spoiler_key: str):
    if file_id:
        await message.answer_voice(file_id)
    else:
        logger.warning(f"Onboarding voice file_id for '{spoiler_key}' is empty")
    await message.answer(_make_spoiler(spoiler_key), parse_mode="HTML")

# ─── /start ───────────────────────────────────────────────────────────────────

@router.message(Command("start"))
async def cmd_start(message: Message, state: FSMContext):
    try:
        await state.clear()
        user = await db.get_or_create_user(
            telegram_id=message.from_user.id,
            username=message.from_user.username
        )

        if not user.get("onboarding_completed", False):
            await _start_onboarding(message, state)
            return

        user_name = user.get("english_name") or user.get("user_name") or ""
        greeting = f"Welcome back{', ' + user_name if user_name else ''}! Choose your mode:"
        await message.answer(greeting, reply_markup=get_mode_keyboard())

    except Exception as e:
        logger.error(f"Error in /start: {e}")
        await message.answer("An error occurred. Please try again.")

# ─── Шаг 1: Голосовое приветствие → просим имя ───────────────────────────────

async def _start_onboarding(message: Message, state: FSMContext):
    await _send_onboarding_voice(message, ONBOARDING_VOICE_START, "start")
    await message.answer(
        "Before we begin — what's your name?\n\n"
        "<i>Just type it below 👇</i>",
        parse_mode="HTML"
    )
    await state.set_state(OnboardingState.waiting_name)

# ─── Шаг 2: Получили имя → LLM предлагает английский вариант ────────────────

@router.message(OnboardingState.waiting_name)
async def onboarding_got_name(message: Message, state: FSMContext):
    try:
        user_name = message.text.strip() if message.text else ""
        if not user_name or len(user_name) > 50:
            await message.answer("Please enter your name (text only).")
            return

        await state.update_data(user_name=user_name)

        from src.services.groq_client import groq_client
        english_name = await groq_client.suggest_english_name(user_name)

        if english_name and english_name.lower() != user_name.lower():
            await state.update_data(english_name_suggestion=english_name)
            await message.answer(
                f"Nice to meet you, {html.escape(user_name)}! 👋\n\n"
                f"In English, <b>{html.escape(user_name)}</b> is often <b>{html.escape(english_name)}</b>. "
                f"Would you like me to call you that?",
                parse_mode="HTML",
                reply_markup=get_english_name_keyboard(english_name)
            )
            await state.set_state(OnboardingState.confirming_english_name)
        else:
            await db.set_user_name(message.from_user.id, user_name, user_name)
            await state.update_data(english_name=user_name)
            await _ask_learning_goal(message, state)

    except Exception as e:
        logger.error(f"Error in onboarding got name: {e}")
        await message.answer("Something went wrong. Please try again.")

# ─── Шаг 3: Подтверждение английского имени ──────────────────────────────────

@router.callback_query(F.data.in_({"onb_name_accept", "onb_name_keep"}), OnboardingState.confirming_english_name)
async def onboarding_name_confirmed(callback: CallbackQuery, state: FSMContext):
    try:
        data = await state.get_data()
        user_name = data.get("user_name", "")
        english_suggestion = data.get("english_name_suggestion", user_name)

        if callback.data == "onb_name_accept":
            english_name = english_suggestion
            await callback.message.answer(
                f"Perfect! I'll call you <b>{html.escape(english_name)}</b> 😊",
                parse_mode="HTML"
            )
        else:
            english_name = user_name
            await callback.message.answer(
                f"Got it — <b>{html.escape(user_name)}</b> it is! 😊",
                parse_mode="HTML"
            )

        await db.set_user_name(callback.from_user.id, user_name, english_name)
        await state.update_data(english_name=english_name)
        await callback.message.edit_reply_markup(reply_markup=None)
        await callback.answer()
        await _ask_learning_goal(callback.message, state)

    except Exception as e:
        logger.error(f"Error in onboarding name confirmed: {e}")
        await callback.answer("Something went wrong.", show_alert=True)

# ─── Шаг 4: Выбор цели обучения ──────────────────────────────────────────────

async def _ask_learning_goal(message: Message, state: FSMContext):
    await message.answer(
        "Why do you want to improve your English?",
        reply_markup=get_learning_goal_keyboard()
    )
    await state.set_state(OnboardingState.choosing_goal)

@router.callback_query(F.data.startswith("onb_goal_"), OnboardingState.choosing_goal)
async def onboarding_goal_selected(callback: CallbackQuery, state: FSMContext):
    try:
        goal = callback.data.replace("onb_goal_", "")
        await db.set_learning_goal(callback.from_user.id, goal)
        await callback.message.edit_reply_markup(reply_markup=None)
        await callback.answer()
        await callback.message.answer(
            "Great! Now, what's your current English level?",
            reply_markup=get_onboarding_level_keyboard()
        )
        await state.set_state(OnboardingState.waiting_level)

    except Exception as e:
        logger.error(f"Error in onboarding goal selection: {e}")
        await callback.answer("An error occurred.", show_alert=True)

# ─── Шаг 5: Выбор уровня ─────────────────────────────────────────────────────

@router.callback_query(F.data.startswith("onb_level_"), OnboardingState.waiting_level)
async def onboarding_level_selected(callback: CallbackQuery, state: FSMContext):
    try:
        level = callback.data.split("_")[2]
        user_id = callback.from_user.id

        await db.update_user_level(user_id, level)
        await callback.message.edit_reply_markup(reply_markup=None)

        voice_map = {
            "beginner":     (ONBOARDING_VOICE_BEGINNER,     "beginner"),
            "intermediate": (ONBOARDING_VOICE_INTERMEDIATE, "intermediate"),
            "advanced":     (ONBOARDING_VOICE_ADVANCED,     "advanced"),
        }
        file_id, spoiler_key = voice_map.get(level, (ONBOARDING_VOICE_INTERMEDIATE, "intermediate"))
        await _send_onboarding_voice(callback.message, file_id, spoiler_key)

        hint = " Tutor is recommended for beginners." if level == "beginner" else ""
        await callback.message.answer(
            f"Ready to start?{(' ' + hint) if hint else ''}",
            reply_markup=get_onboarding_mode_keyboard(level)
        )
        await state.update_data(onb_level=level)
        await state.set_state(OnboardingState.choosing_mode)
        await callback.answer()

    except Exception as e:
        logger.error(f"Error in onboarding level selection: {e}")
        await callback.answer("An error occurred.", show_alert=True)

# ─── Шаг 6: Выбор режима ─────────────────────────────────────────────────────

@router.callback_query(F.data.startswith("onb_mode_"), OnboardingState.choosing_mode)
async def onboarding_mode_selected(callback: CallbackQuery, state: FSMContext):
    try:
        mode_key = callback.data.split("_")[2]
        user_id = callback.from_user.id

        await db.complete_onboarding(user_id)
        await callback.message.edit_reply_markup(reply_markup=None)

        data = await state.get_data()
        english_name = data.get("english_name", "")

        from src.modes import MODE_TUTOR, MODE_PENFRIEND, MODE_FLOW
        from src.personas import get_persona_voice

        if mode_key == "tutor":
            await db.update_mode(user_id, MODE_TUTOR)
            await db.update_user_persona(user_id, "mrs_smith")
            await db.update_user_voice(user_id, get_persona_voice("mrs_smith"))
            name_part = f", {english_name}" if english_name else ""
            await callback.message.answer(
                f"📚 Mrs. Smith is your guide{name_part}. Switch to PenFriend or Flow anytime.",
                reply_markup=get_mode_keyboard()
            )
            from src.services.groq_client import groq_client
            user_data = await db.get_or_create_user(user_id)
            user_level = user_data.get("level", "beginner")
            greeting = await groq_client.generate_persona_greeting(
                "mrs_smith", user_level, session_count=0, user_name=english_name
            )
            voice_bytes = await groq_client.text_to_speech(greeting, voice="diana")
            if voice_bytes:
                voice_file = BufferedInputFile(voice_bytes, filename="greeting.wav")
                await callback.message.answer_voice(voice_file, caption="📚 Mrs. Smith")
                await callback.message.answer(
                    f"<blockquote expandable>📚 {html.escape(greeting)}</blockquote>",
                    parse_mode="HTML"
                )
            else:
                await callback.message.answer(f"📚 {html.escape(greeting)}", parse_mode="HTML")
            await db.save_message(user_id, "assistant", greeting)

        elif mode_key == "penfriend":
            await db.update_mode(user_id, MODE_PENFRIEND)
            await callback.message.answer(
                "✉️ PenFriend Mode — choose who you want to write to:",
                reply_markup=get_mode_keyboard()
            )
        elif mode_key == "flow":
            await db.update_mode(user_id, MODE_FLOW)
            await callback.message.answer(
                "🎙 Flow Mode — choose who you want to talk to:",
                reply_markup=get_mode_keyboard()
            )

        await state.clear()
        await callback.answer()

    except Exception as e:
        logger.error(f"Error in onboarding mode selection: {e}")
        await callback.answer("Something went wrong.", show_alert=True)


# ─── Хендлеры для получения file_id голосовых онбординга ─────────────────────
# РАСКОММЕНТИРОВАТЬ если нужно перезаписать голосовые в config.py
#
# @router.message(F.voice, lambda m: m.from_user.id in ADMIN_IDS)
# async def get_voice_file_id(message: Message):
#     file_id = message.voice.file_id
#     duration = message.voice.duration
#     await message.answer(
#         f"🎤 <b>Voice file_id</b> ({duration}s):\n\n"
#         f"<code>{file_id}</code>\n\n"
#         f"Вставь в нужную константу ONBOARDING_VOICE_* в config.py",
#         parse_mode="HTML"
#     )
#
# @router.message(
#     F.document,
#     F.document.file_name.func(lambda name: name and name.endswith((".wav", ".ogg", ".mp3"))),
#     lambda m: m.from_user.id in ADMIN_IDS
# )
# async def get_document_file_id(message: Message):
#     file_id = message.document.file_id
#     file_name = message.document.file_name or "unknown"
#     await message.answer(
#         f"📎 <b>{html.escape(file_name)}</b>\n\n"
#         f"<code>{file_id}</code>\n\n"
#         f"⚠️ Это document file_id — нужен voice file_id для онбординга.",
#         parse_mode="HTML"
#     )
