import logging
import html
from aiogram import Router, F
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.types import Message, CallbackQuery, BufferedInputFile

from src.bot.handlers.states import OnboardingState
from src.bot.keyboards import get_level_keyboard, get_mode_keyboard
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

# ─── Клавиатура уровней для онбординга (без кнопки Back) ──────────────────────

def get_onboarding_level_keyboard():
    from aiogram.utils.keyboard import InlineKeyboardBuilder
    from aiogram.types import InlineKeyboardButton
    builder = InlineKeyboardBuilder()
    builder.row(
        InlineKeyboardButton(text="Beginner",     callback_data="onb_level_beginner"),
        InlineKeyboardButton(text="Intermediate", callback_data="onb_level_intermediate"),
    )
    builder.row(
        InlineKeyboardButton(text="Advanced",     callback_data="onb_level_advanced"),
    )
    return builder.as_markup()

# ─── Хелпер: спойлер под голосовым ────────────────────────────────────────────

def _make_spoiler(key: str) -> str:
    data = ONBOARDING_SPOILERS.get(key, {})
    en = html.escape(data.get("en", ""))
    ru = html.escape(data.get("ru", ""))
    return f"<blockquote expandable>{en}\n\n{ru}</blockquote>"

# ─── Хелпер: отправить голосовое онбординга ───────────────────────────────────

async def _send_onboarding_voice(message: Message, file_id: str, spoiler_key: str):
    """Отправляет голосовое по file_id + спойлер с текстом и переводом."""
    if file_id:
        await message.answer_voice(file_id)
    else:
        # Заглушка если file_id ещё не заполнен
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

        # Новый пользователь → онбординг
        if not user.get("onboarding_completed", False):
            await _start_onboarding(message, state)
            return

        # Существующий пользователь → просто сброс состояния + меню
        await message.answer(
            "Welcome back! Choose your mode:",
            reply_markup=get_mode_keyboard()
        )

    except Exception as e:
        logger.error(f"Error in /start: {e}")
        await message.answer("An error occurred. Please try again.")

async def _start_onboarding(message: Message, state: FSMContext):
    """Шаг 1 онбординга: голосовое приветствие + выбор уровня."""
    await _send_onboarding_voice(message, ONBOARDING_VOICE_START, "start")
    await message.answer(
        "Choose your level:",
        reply_markup=get_onboarding_level_keyboard()
    )
    await state.set_state(OnboardingState.waiting_level)

# ─── Онбординг: выбор уровня ──────────────────────────────────────────────────

@router.callback_query(F.data.startswith("onb_level_"), OnboardingState.waiting_level)
async def onboarding_level_selected(callback: CallbackQuery, state: FSMContext):
    try:
        level = callback.data.split("_")[2]  # onb_level_beginner → beginner
        user_id = callback.from_user.id

        await db.update_user_level(user_id, level)
        await callback.message.edit_reply_markup(reply_markup=None)

        # Голосовое реакции на уровень
        voice_map = {
            "beginner":     (ONBOARDING_VOICE_BEGINNER,     "beginner"),
            "intermediate": (ONBOARDING_VOICE_INTERMEDIATE, "intermediate"),
            "advanced":     (ONBOARDING_VOICE_ADVANCED,     "advanced"),
        }
        file_id, spoiler_key = voice_map.get(level, (ONBOARDING_VOICE_INTERMEDIATE, "intermediate"))
        await _send_onboarding_voice(callback.message, file_id, spoiler_key)

        await state.set_state(OnboardingState.waiting_first_message)
        await callback.answer()

    except Exception as e:
        logger.error(f"Error in onboarding level selection: {e}")
        await callback.answer("An error occurred.", show_alert=True)

# ─── Онбординг: первое сообщение пользователя ────────────────────────────────

@router.message(OnboardingState.waiting_first_message)
async def onboarding_first_message(message: Message, state: FSMContext):
    """
    Любое сообщение в этом состоянии = пользователь начал говорить.
    Завершаем онбординг, переводим в Tutor Mode (Mrs. Smith по умолчанию).
    """
    try:
        user_id = message.from_user.id
        await db.complete_onboarding(user_id)
        await state.clear()

        # Активируем Tutor Mode с Mrs. Smith как точку входа после онбординга
        from src.modes import MODE_TUTOR
        from src.personas import get_persona_voice
        await db.update_mode(user_id, MODE_TUTOR)
        await db.update_user_persona(user_id, "mrs_smith")
        await db.update_user_voice(user_id, get_persona_voice("mrs_smith"))

        await message.answer(
            "🎓 <b>Tutor Mode</b> — 📚 Mrs. Smith is listening.\n\n"
            "Send a voice or text message to begin.",
            parse_mode="HTML",
            reply_markup=get_mode_keyboard()
        )

        # Передаём сообщение дальше в основной хендлер Tutor
        # (re-dispatch через FSM невозможен, поэтому просто обрабатываем текст здесь)
        from src.bot.handlers.message import handle_message
        await handle_message(message, state)

    except Exception as e:
        logger.error(f"Error in onboarding first message: {e}")
        await message.answer("Something went wrong. Please try again.")

# ─── Временный хендлер для получения file_id голосовых ───────────────────────
# УДАЛИТЬ после заполнения ONBOARDING_VOICE_* в config.py

@router.message(F.document, lambda m: m.from_user.id in ADMIN_IDS)
async def get_file_id(message: Message):
    """
    Отправь боту любой WAV как документ — получишь file_id в ответ.
    Только для администраторов. Удалить после заполнения config.py.
    """
    file_id = message.document.file_id
    file_name = message.document.file_name or "unknown"
    await message.answer(
        f"📎 <b>{html.escape(file_name)}</b>\n\n"
        f"<code>{file_id}</code>\n\n"
        f"Скопируй в config.py в нужную константу ONBOARDING_VOICE_*",
        parse_mode="HTML"
    )
    
@router.message(F.voice, lambda m: m.from_user.id in ADMIN_IDS)
async def get_voice_file_id(message: Message):
    file_id = message.voice.file_id
    await message.answer(
        f"🎤 voice file_id:\n\n<code>{file_id}</code>",
        parse_mode="HTML"
    )
