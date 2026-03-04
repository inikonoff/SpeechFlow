import logging
import html
import re
from typing import Dict
from aiogram import Router, F
from aiogram.types import Message, CallbackQuery, InlineKeyboardButton
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.utils.keyboard import InlineKeyboardBuilder

from src.bot.keyboards import (
    get_level_keyboard,
    get_persona_keyboard,
    get_translate_keyboard,
    get_original_keyboard,
    get_settings_keyboard,
    get_back_to_menu_keyboard,
    get_main_menu_keyboard,
)
from src.personas import get_all_personas
from src.services.supabase_db import db
from src.services.groq_client import groq_client

router = Router()
logger = logging.getLogger(__name__)

_how_to_originals: Dict[int, str] = {}

HOW_TO_TEXT = """🗣 <b>How to use Speech Flow AI</b>

<b>Normal Mode</b>
1. <b>Speak or write in English</b> — your message is analyzed for errors
2. <b>Natural conversation</b> — the bot keeps the dialogue flowing
3. <b>Integrated corrections</b> — mistakes are corrected with explanation in Russian
4. <b>Vocabulary building</b> — new words are saved to your dictionary automatically
5. <b>Voice messages recommended</b> — speaking practice is the fastest path to fluency

<b>▶ Flow Mode</b>
6. <b>Press ▶ Flow</b> — no corrections, no analysis, just real conversation
7. <b>Choose your conversation partner</b> — six different people, each with their own personality and voice
8. <b>Talk freely</b> — your partner listens and responds naturally
9. <b>Switch partners anytime</b> — tap Switch and your new partner picks up the thread
10. <b>Press ⏹ Stop Flow</b> to return to Normal Mode

💡 <b>Tips</b>
• Flow Mode is where real fluency happens — use it often
• The bot will occasionally remind you of saved words — try to use them
• Check your stats to track progress over time"""


# ─── Mastery helpers ───────────────────────────────────────────────────────

def mastery_label(score: int) -> str:
    if score == 0:
        return "🆕"
    elif score <= 2:
        return "📖"
    elif score <= 4:
        return "🔄"
    else:
        return "✅"


def trend_arrow(current: int, previous: int) -> str:
    if previous == 0:
        return ""
    if current < previous:
        return f" ↓{previous - current}"
    elif current > previous:
        return f" ↑{current - previous}"
    return " →"


# ─── Команды ───────────────────────────────────────────────────────────────

@router.message(Command("stats"))
async def cmd_stats(message: Message):
    await _send_stats(message.from_user.id, message.answer)


@router.message(Command("vocabulary"))
async def cmd_vocabulary(message: Message):
    await _send_vocabulary(message.from_user.id, message.answer)


@router.message(Command("voice"))
async def cmd_voice(message: Message):
    personas = get_all_personas()
    builder = InlineKeyboardBuilder()
    for key, name in personas.items():
        builder.row(InlineKeyboardButton(text=name, callback_data=f"persona_{key}"))
    await message.answer(
        "🗣 <b>Choose your conversation partner:</b>",
        reply_markup=builder.as_markup(),
        parse_mode="HTML"
    )


@router.message(Command("settings"))
async def cmd_settings(message: Message):
    user = await db.get_or_create_user(message.from_user.id)
    notif = user.get("notifications_enabled", True)
    await message.answer(
        "⚙️ <b>Settings</b>",
        parse_mode="HTML",
        reply_markup=get_settings_keyboard(notif)
    )


@router.message(Command("author"))
async def cmd_author(message: Message):
    await message.answer(
        "👨‍💻 <b>SpeechFlow AI</b>\n\n"
        "Created by: @inikonoff\n"
        "Feedback and suggestions are welcome!",
        parse_mode="HTML"
    )


# ─── Stats & Vocabulary helpers ────────────────────────────────────────────

async def _send_stats(user_id: int, send_fn):
    try:
        stats = await db.get_user_stats(user_id)
        user = stats.get("user", {})

        level = str(user.get('level', 'Not set')).upper()
        streak = user.get('streak_days', 0)
        persona = user.get('persona', 'greg').capitalize()
        vocab_count = stats.get("vocabulary_count", 0)
        mastered = stats.get("mastered_count", 0)
        msgs_this = stats.get("msgs_this_week", 0)
        msgs_prev = stats.get("msgs_prev_week", 0)
        error_week = stats.get("error_stats_week", {})
        error_prev = stats.get("error_stats_prev_week", {})

        activity_line = f"• Messages this week: <b>{msgs_this}</b>"
        if msgs_prev > 0:
            activity_line += f"  <i>({trend_arrow(msgs_this, msgs_prev).strip() or '→'} vs last week)</i>"

        vocab_line = f"• Vocabulary: <b>{vocab_count}</b> words"
        if mastered > 0:
            vocab_line += f", <b>{mastered}</b> mastered ✅"

        stats_text = (
            f"📊 <b>Your Speech Flow Stats</b>\n\n"
            f"👤 <b>Profile</b>\n"
            f"• Level: <b>{level}</b>\n"
            f"• Partner: <b>{persona}</b>\n"
            f"• Streak: <b>{streak} days</b>\n\n"
            f"📈 <b>This week</b>\n"
            f"{activity_line}\n"
            f"{vocab_line}\n\n"
            f"🎯 <b>Errors this week</b>\n"
        )

        all_categories = set(list(error_week.keys()) + list(error_prev.keys()))
        if all_categories:
            for cat in sorted(all_categories):
                cur = error_week.get(cat, 0)
                prev = error_prev.get(cat, 0)
                arrow = trend_arrow(cur, prev)
                cat_safe = html.escape(str(cat))
                stats_text += f"• {cat_safe}: <b>{cur}</b>{arrow}\n"
            stats_text += "\n<i>↓ = improving, ↑ = more errors, → = same</i>"
        else:
            stats_text += "No errors logged yet. Keep practicing!"

        await send_fn(stats_text, parse_mode="HTML")
    except Exception as e:
        logger.error(f"Error in stats: {e}")
        await send_fn("Error loading stats.", parse_mode="HTML")


async def _send_vocabulary(user_id: int, send_fn):
    try:
        vocabulary = await db.get_user_vocabulary(user_id, limit=20)

        if not vocabulary:
            await send_fn(
                "📚 <b>Your Vocabulary</b>\n\n"
                "Empty so far. Words from your conversations will appear here automatically.",
                parse_mode="HTML"
            )
            return

        vocab_text = "📚 <b>Your Vocabulary</b>\n\n"
        vocab_text += "<i>🆕 New  📖 Learning  🔄 Reviewing  ✅ Mastered</i>\n\n"

        for i, item in enumerate(vocabulary, 1):
            word = html.escape(item.get("word_or_phrase", ""))
            translation = html.escape(item.get("translation", ""))
            context = html.escape(item.get("context_sentence", ""))
            score = item.get("mastery_score", 0)
            label = mastery_label(score)

            vocab_text += f"{i}. {label} <b>{word}</b> — {translation}\n"
            if context:
                short = context[:60] + "..." if len(context) > 60 else context
                vocab_text += f"   <i>\"{short}\"</i>\n"
            vocab_text += "\n"

        await send_fn(vocab_text, parse_mode="HTML")
    except Exception as e:
        logger.error(f"Error in vocabulary: {e}")
        await send_fn("Error loading vocabulary.", parse_mode="HTML")


# ─── Callbacks ─────────────────────────────────────────────────────────────

@router.callback_query(F.data == "how_to_use")
async def show_how_to_use(callback: CallbackQuery):
    try:
        sent = await callback.message.edit_text(HOW_TO_TEXT, parse_mode="HTML")
        _how_to_originals[sent.message_id] = HOW_TO_TEXT
        await sent.edit_reply_markup(reply_markup=get_translate_keyboard(sent.message_id))
        await callback.answer()
    except Exception as e:
        logger.error(f"Error showing how to use: {e}")
        await callback.answer("Error.", show_alert=True)


@router.callback_query(F.data == "settings")
async def show_settings(callback: CallbackQuery):
    try:
        user = await db.get_or_create_user(callback.from_user.id)
        notif = user.get("notifications_enabled", True)
        await callback.message.edit_text(
            "⚙️ <b>Settings</b>",
            parse_mode="HTML",
            reply_markup=get_settings_keyboard(notif)
        )
        await callback.answer()
    except Exception as e:
        logger.error(f"Error in settings: {e}")
        await callback.answer("Error.", show_alert=True)


@router.callback_query(F.data == "toggle_notifications")
async def toggle_notifications(callback: CallbackQuery):
    try:
        user = await db.get_or_create_user(callback.from_user.id)
        current = user.get("notifications_enabled", True)
        new_value = not current
        await db.update_notifications(callback.from_user.id, new_value)

        status = "ON 🔔" if new_value else "OFF 🔕"
        await callback.answer(f"Notifications {status}", show_alert=False)

        await callback.message.edit_reply_markup(
            reply_markup=get_settings_keyboard(new_value)
        )
    except Exception as e:
        logger.error(f"Error toggling notifications: {e}")
        await callback.answer("Error.", show_alert=True)


@router.callback_query(F.data == "change_level")
async def change_user_level(callback: CallbackQuery):
    await callback.message.edit_text(
        "<b>Select your new English level:</b>",
        reply_markup=get_level_keyboard(),
        parse_mode="HTML"
    )
    await callback.answer()


@router.callback_query(F.data == "back_to_menu")
async def back_to_menu(callback: CallbackQuery):
    try:
        await callback.message.edit_text(
            "🏠 <b>Main Menu</b>",
            parse_mode="HTML",
            reply_markup=get_main_menu_keyboard()
        )
        await callback.answer()
    except Exception as e:
        logger.error(f"Error in back_to_menu: {e}")
        await callback.answer("Error.", show_alert=True)


@router.callback_query(F.data == "correction_rate")
async def show_correction_rate(callback: CallbackQuery):
    try:
        from src.bot.keyboards import get_correction_rate_keyboard
        from src.modes import correction_rate_label
        user = await db.get_or_create_user(callback.from_user.id)
        rate = user.get("correction_rate", 50)
        await callback.message.edit_text(
            f"✏️ <b>Correction Sensitivity</b>\n\n"
            f"Current: <b>{correction_rate_label(rate)}</b>\n\n"
            f"😌 <b>Relaxed</b> — only serious errors that confuse native speakers\n"
            f"⚖️ <b>Balanced</b> — clear grammatical errors corrected\n"
            f"🎯 <b>Strict</b> — most errors corrected naturally",
            parse_mode="HTML",
            reply_markup=get_correction_rate_keyboard(rate)
        )
        await callback.answer()
    except Exception as e:
        logger.error(f"Error in correction_rate: {e}")
        await callback.answer("Error.", show_alert=True)


@router.callback_query(F.data.startswith("set_correction_"))
async def set_correction_rate(callback: CallbackQuery):
    try:
        from src.bot.keyboards import get_correction_rate_keyboard
        from src.modes import correction_rate_label
        rate = int(callback.data.split("_")[2])
        await db.update_correction_rate(callback.from_user.id, rate)
        await callback.message.edit_reply_markup(
            reply_markup=get_correction_rate_keyboard(rate)
        )
        await callback.answer(f"Set to {correction_rate_label(rate)}")
    except Exception as e:
        logger.error(f"Error in set_correction_rate: {e}")
        await callback.answer("Error.", show_alert=True)


@router.callback_query(F.data == "my_stats")
async def cq_show_stats(callback: CallbackQuery):
    try:
        await _send_stats(
            callback.from_user.id,
            lambda text, **kwargs: callback.message.edit_text(text, **kwargs)
        )
        await callback.answer()
    except Exception as e:
        logger.error(f"Error in cq_show_stats: {e}")
        await callback.answer("Error loading stats.", show_alert=True)


@router.callback_query(F.data == "my_vocabulary")
async def cq_show_vocab(callback: CallbackQuery):
    try:
        await _send_vocabulary(
            callback.from_user.id,
            lambda text, **kwargs: callback.message.edit_text(text, **kwargs)
        )
        await callback.answer()
    except Exception as e:
        logger.error(f"Error in cq_show_vocab: {e}")
        await callback.answer("Error loading vocabulary.", show_alert=True)


# ─── Translate / Original для How to use ──────────────────────────────────

@router.callback_query(F.data.startswith("howto_translate_"))
async def howto_translate(callback: CallbackQuery):
    try:
        message_id = int(callback.data.split("_")[2])
        original = _how_to_originals.get(message_id, HOW_TO_TEXT)
        clean_text = re.sub(r'<[^>]+>', '', original)
        translation = await groq_client.translate_text(clean_text)
        safe_translation = html.escape(translation)
        await callback.message.edit_text(
            f"🌐 {safe_translation}",
            parse_mode="HTML",
            reply_markup=get_original_keyboard(message_id)
        )
        await callback.answer()
    except Exception as e:
        logger.error(f"Error translating how to use: {e}")
        await callback.answer("Translation failed.", show_alert=True)


@router.callback_query(F.data.startswith("howto_original_"))
async def howto_original(callback: CallbackQuery):
    try:
        message_id = int(callback.data.split("_")[2])
        await callback.message.edit_text(
            HOW_TO_TEXT,
            parse_mode="HTML",
            reply_markup=get_translate_keyboard(message_id)
        )
        await callback.answer()
    except Exception as e:
        logger.error(f"Error restoring how to use: {e}")
        await callback.answer("Could not restore original.", show_alert=True)
