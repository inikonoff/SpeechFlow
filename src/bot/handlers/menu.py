import logging
import html
import re
import asyncio
from typing import Dict
from aiogram import Router, F
from aiogram.types import Message, CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from src.bot.handlers.states import FlowState, AdminState
from aiogram.utils.keyboard import InlineKeyboardBuilder

from src.bot.keyboards import (
    get_level_keyboard,
    get_persona_keyboard,
    get_translate_keyboard,
    get_original_keyboard,
    get_settings_keyboard,
    get_back_to_menu_keyboard,
    get_main_menu_keyboard,
    get_admin_panel_keyboard,
    get_admin_stats_keyboard,
    get_admin_users_keyboard,
    get_admin_user_card_keyboard,
    get_stats_back_keyboard,
)
from src.personas import get_all_personas
from src.services.supabase_db import db
from src.services.groq_client import groq_client
from src.config import ADMIN_IDS

router = Router()
logger = logging.getLogger(__name__)

_how_to_originals: Dict[int, str] = {}

HOW_TO_TEXT = """🗣 <b>Speech Flow Pro — How it works</b>

<b>🎓 Tutor Mode</b>
Speak or write in English. Mrs. Smith corrects mistakes naturally and explains them in Russian. No red pen — just a conversation that makes you better.

<b>✉️ PenFriend Mode</b>
Text chat with one of six characters. Each has their own life, personality, and way of speaking. Your English is shaped gently without breaking the flow.

<b>🎙 Flow Mode</b>
Pure voice conversation with a character. No corrections, no analysis — just real talk. This is where fluency actually happens. Use <b>↩ Switch</b> to change partners anytime.

<b>👥 Characters</b>
Greg 🧑‍⚕️ · Mark 👨‍🍳 · Junior 👨‍💻 · Mrs. Smith 👩‍🏫 · Summer 🌍 · Jane ☕
Six people, one small world. Each remembers your conversations.

<b>📚 Vocabulary &amp; Stats</b>
New words are saved automatically as you chat. Use /stats to track your progress and /vocabulary to review your words.

💡 Flow Mode is where real fluency happens — use it often."""

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

@router.message(Command("settings"))
async def cmd_settings(message: Message):
    user = await db.get_or_create_user(message.from_user.id)
    notif = user.get("notifications_enabled", True)
    practice = user.get("vocab_practice_enabled", False)
    await message.answer(
        "⚙️ <b>Settings</b>",
        parse_mode="HTML",
        reply_markup=get_settings_keyboard(notif, message.from_user.id, practice)
    )

@router.message(Command("author"))
async def cmd_author(message: Message):
    await message.answer(
        "👨‍💻 <b>SpeechFlow Pro</b>\n\n"
        "Created by: @inikonoff\n"
        "Feedback and suggestions are welcome!",
        parse_mode="HTML"
    )

# ─── Stats cache ──────────────────────────────────────────────────────────
_stats_cache: Dict[int, dict] = {} 

def _cache_stats(user_id: int, stats: dict):
    if len(_stats_cache) > 2000:
        _stats_cache.clear()
    _stats_cache[user_id] = stats

def _build_quick_stats(stats: dict) -> str:
    user = stats.get("user", {})
    level = str(user.get("level", "Not set")).upper()
    streak = user.get("streak_days", 0)
    persona = user.get("persona", "greg").capitalize()
    vocab_count = stats.get("vocabulary_count", 0)
    mastered = stats.get("mastered_count", 0)
    msgs_this = stats.get("msgs_this_week", 0)
    msgs_prev = stats.get("msgs_prev_week", 0)
    error_week = stats.get("error_stats_week", {})
    error_prev = stats.get("error_stats_prev_week", {})

    if streak == 0:
        streak_line = "• Streak: just getting started 🌱"
    elif streak == 1:
        streak_line = "• Streak: <b>1 day</b> — good start 🔥"
    elif streak < 7:
        streak_line = f"• Streak: <b>{streak} days</b> 🔥"
    else:
        streak_line = f"• Streak: <b>{streak} days</b> 🔥🔥"

    activity_line = f"• Messages this week: <b>{msgs_this}</b>"
    if msgs_prev > 0:
        arrow = trend_arrow(msgs_this, msgs_prev).strip()
        activity_line += f"  <i>({arrow or '→'} vs last week)</i>"

    vocab_line = f"• Vocabulary: <b>{vocab_count}</b> words"
    if mastered > 0:
        pct = int(mastered / vocab_count * 100) if vocab_count else 0
        vocab_line += f" · <b>{mastered}</b> mastered ({pct}%) ✅"

    text = (
        f"📊 <b>Your Speech Flow Stats</b>\n\n"
        f"👤 <b>Profile</b>\n"
        f"• Level: <b>{level}</b>\n"
        f"• Partner: <b>{persona}</b>\n"
        f"{streak_line}\n\n"
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
            text += f"• {cat_safe}: <b>{cur}</b>{arrow}\n"
        text += "\n<i>↓ = improving  ↑ = more errors  → = same</i>"
    else:
        text += "No errors logged yet — keep practicing!"

    return text

def get_stats_keyboard(message_id: int, mode: str = "quick") -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    if mode == "quick":
        builder.row(InlineKeyboardButton(text="📖 Deep Dive", callback_data=f"stats_deep_{message_id}"))
    else:
        builder.row(InlineKeyboardButton(text="📊 Quick Stats", callback_data=f"stats_quick_{message_id}"))
    builder.row(InlineKeyboardButton(text="🌐 Translate", callback_data=f"stats_translate_{message_id}_{mode}"))
    return builder.as_markup()

async def _send_stats(user_id: int, send_fn, edit_msg=None):
    try:
        stats = await db.get_user_stats(user_id)
        _cache_stats(user_id, stats)
        text = _build_quick_stats(stats)

        if edit_msg:
            sent = await edit_msg(text, parse_mode="HTML", reply_markup=None)
        else:
            sent = await send_fn(text, parse_mode="HTML")

        await sent.edit_reply_markup(reply_markup=get_stats_keyboard(sent.message_id, "quick"))
    except Exception as e:
        logger.error(f"Error in stats: {e}")
        if edit_msg:
            await edit_msg("Error loading stats.", parse_mode="HTML")
        else:
            await send_fn("Error loading stats.", parse_mode="HTML")

# ─── Vocabulary helpers ────────────────────────────────────────────────────

TAB_LABELS = {"active": "🔥 Active", "all": "📖 All", "difficult":"⭐ Difficult", "mastered": "✅ Mastered"}

def _vocab_tab_keyboard(current_tab: str) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    tabs = ["active", "all", "difficult", "mastered"]
    row = []
    for t in tabs:
        label = f"· {TAB_LABELS[t]} ·" if t == current_tab else TAB_LABELS[t]
        row.append(InlineKeyboardButton(text=label, callback_data=f"vocab_tab_{t}"))
    builder.row(*row)
    return builder.as_markup()

def _build_vocab_text(vocabulary: list, tab: str) -> str:
    tab_label = TAB_LABELS.get(tab, "📖 Vocabulary")
    if not vocabulary:
        empty_msgs = {
            "active": "No active words — keep chatting and they'll appear here.",
            "difficult": "No difficult words yet. Great progress!",
            "mastered": "No mastered words yet — keep practicing!",
            "all": "Empty so far. Words from your conversations will appear here automatically.",
        }
        return f"📚 <b>Your Vocabulary — {tab_label}</b>\n\n{empty_msgs.get(tab, '')}"

    text = f"📚 <b>Your Vocabulary — {tab_label}</b>  <i>({len(vocabulary)} words)</i>\n\n"
    for i, item in enumerate(vocabulary, 1):
        word = html.escape(item.get("word_or_phrase", ""))
        translation = html.escape(item.get("translation", ""))
        context = html.escape(item.get("context_sentence", ""))
        score = item.get("mastery_score", 0)
        reminded = item.get("times_reminded", 0)
        used = item.get("times_used", 0)
        wtype = item.get("word_type", "")

        type_badge = {"phrasal_verb": "🔗", "collocation": "🔀", "grammar_pattern": "📐", "phrase": "💬"}.get(wtype, "")
        mastery_bar = "█" * score + "░" * (5 - score)

        text += f"{i}. {type_badge}<b>{word}</b> — {translation}\n"
        if context:
            short = context[:55] + "…" if len(context) > 55 else context
            text += f"   <i>\"{short}\"</i>\n"
        text += f"   <code>{mastery_bar}</code>"
        if reminded > 0:
            text += f"  reminded: {reminded}"
        if used > 0:
            text += f"  used: {used}"
        text += "\n\n"
    return text

async def _send_vocabulary(user_id: int, send_fn, tab: str = "active", edit_msg=None):
    try:
        vocabulary = await db.get_user_vocabulary(user_id, tab=tab, limit=20)
        text = _build_vocab_text(vocabulary, tab)
        kb = _vocab_tab_keyboard(tab)

        if edit_msg:
            await edit_msg(text, parse_mode="HTML", reply_markup=kb)
        else:
            await send_fn(text, parse_mode="HTML", reply_markup=kb)
    except Exception as e:
        logger.error(f"Error in vocabulary: {e}")
        if edit_msg:
            await edit_msg("Error loading vocabulary.", parse_mode="HTML")
        else:
            await send_fn("Error loading vocabulary.", parse_mode="HTML")

# ─── Callbacks ─────────────────────────────────────────────────────────────

@router.callback_query(F.data == "how_to_use")
async def show
