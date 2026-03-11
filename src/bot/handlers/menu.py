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

<b>📋 Mistakes Practice &amp; Stats</b>
Your mistakes are tracked automatically. Open ⚙️ Settings → Mistakes Practice to review your patterns, or use /stats for progress.

💡 Flow Mode is where real fluency happens — use it often."""

# ─── Mastery helpers ───────────────────────────────────────────────────────

ERROR_MASTERY_THRESHOLD = 3

def mastery_label(score: int) -> str:
    if score == 0:
        return "🆕"
    elif score == 1:
        return "📖"
    elif score == 2:
        return "🔄"
    else:
        return "✅"  # score >= 3

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
    await message.answer(
        "⚙️ <b>Settings</b>",
        parse_mode="HTML",
        reply_markup=get_settings_keyboard(notif, message.from_user.id)
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
    active_errors = stats.get("active_errors_count", 0)
    mastered_errors = stats.get("mastered_errors_count", 0)
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

    practice_line = f"• Practice Log: <b>{active_errors}</b> active"
    if mastered_errors > 0:
        practice_line += f" · <b>{mastered_errors}</b> mastered ✅"

    text = (
        f"📊 <b>Your Speech Flow Stats</b>\n\n"
        f"👤 <b>Profile</b>\n"
        f"• Level: <b>{level}</b>\n"
        f"• Partner: <b>{persona}</b>\n"
        f"{streak_line}\n\n"
        f"📈 <b>This week</b>\n"
        f"{activity_line}\n"
        f"{practice_line}\n\n"
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

# ─── Practice Log helpers ──────────────────────────────────────────────────

PRACTICE_TAB_LABELS = {"mistakes": "📖 Mistakes", "mastered": "✅ Mastered"}

def _practice_tab_keyboard(current_tab: str) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    tabs = ["mistakes", "mastered"]
    row = []
    for t in tabs:
        label = f"· {PRACTICE_TAB_LABELS[t]} ·" if t == current_tab else PRACTICE_TAB_LABELS[t]
        row.append(InlineKeyboardButton(text=label, callback_data=f"practice_tab_{t}"))
    builder.row(*row)
    return builder.as_markup()

def _build_practice_text(errors: list, tab: str) -> str:
    tab_label = PRACTICE_TAB_LABELS.get(tab, "📋 Mistakes Practice")
    if not errors:
        empty_msgs = {
            "mistakes": "No active mistakes yet — keep chatting and they'll appear here.",
            "mastered": "Nothing mastered yet — keep practicing!",
        }
        return f"📋 <b>Mistakes Practice — {tab_label}</b>\n\n{empty_msgs.get(tab, '')}\n\n<i>Mistakes are tracked automatically from your conversations.</i>"

    text = f"📋 <b>Mistakes Practice — {tab_label}</b>  <i>({len(errors)} patterns)</i>\n\n"
    for i, item in enumerate(errors, 1):
        category  = html.escape(item.get("category")  or "unknown")
        mistake   = html.escape(item.get("mistake_text")   or "")
        corrected = html.escape(item.get("corrected_text") or "")
        score     = item.get("mastery_score", 0) or 0
        times     = item.get("times_corrected", 0) or 0

        mastery_bar = "█" * score + "░" * max(0, ERROR_MASTERY_THRESHOLD - score)
        text += f"{i}. 📌 <b>{category}</b>\n"
        if mistake:
            short_m = (mistake[:60] + "…") if len(mistake) > 60 else mistake
            text += f"   ❌ <i>{short_m}</i>\n"
        if corrected:
            short_c = (corrected[:60] + "…") if len(corrected) > 60 else corrected
            text += f"   ✅ {short_c}\n"
        text += f"   <code>{mastery_bar}</code>  {score}/{ERROR_MASTERY_THRESHOLD}"
        if times > 0:
            text += f"  ·  corrected {times}×"
        text += "\n\n"
    return text

async def _send_practice_log(user_id: int, send_fn, tab: str = "mistakes", edit_msg=None):
    try:
        errors = await db.get_user_errors(user_id, tab=tab)
        text = _build_practice_text(errors, tab)
        kb = _practice_tab_keyboard(tab)

        if edit_msg:
            await edit_msg(text, parse_mode="HTML", reply_markup=kb)
        else:
            await send_fn(text, parse_mode="HTML", reply_markup=kb)
    except Exception as e:
        import traceback
        logger.error(f"Error in practice log: {e}\n{traceback.format_exc()}")
        try:
            msg = "⚠️ Error loading Mistakes Practice."
            if edit_msg:
                await edit_msg(msg, parse_mode="HTML")
            elif send_fn:
                await send_fn(msg, parse_mode="HTML")
        except Exception:
            pass

# ─── Callbacks ─────────────────────────────────────────────────────────────

@router.callback_query(F.data == "how_to_use")
async def show_how_to_use(callback: CallbackQuery):
    try:
        sent = await callback.message.edit_text(HOW_TO_TEXT, parse_mode="HTML")
        if len(_how_to_originals) > 1000: _how_to_originals.clear()
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
            reply_markup=get_settings_keyboard(notif, callback.from_user.id)
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
            reply_markup=get_settings_keyboard(new_value, callback.from_user.id)
        )
    except Exception as e:
        logger.error(f"Error toggling notifications: {e}")
        await callback.answer("Error.", show_alert=True)

@router.callback_query(F.data == "change_level")
async def cq_change_level(callback: CallbackQuery):
    await callback.message.edit_text("Select your English level:", reply_markup=get_level_keyboard())
    await callback.answer()

@router.callback_query(F.data == "back_to_menu")
async def back_to_menu(callback: CallbackQuery):
    try:
        await callback.message.edit_text(
            "🏠 <b>Main Menu</b>",
            parse_mode="HTML",
            reply_markup=get_main_menu_keyboard(callback.from_user.id)
        )
        await callback.answer()
    except Exception as e:
        logger.error(f"Error in back_to_menu: {e}")
        await callback.answer("Error.", show_alert=True)

# ─── Админ-панель ──────────────────────────────────────────────────────────

def _admin_only(func):
    from functools import wraps
    @wraps(func)
    async def wrapper(callback: CallbackQuery, **kwargs):
        if callback.from_user.id not in ADMIN_IDS:
            await callback.answer("⛔ Нет доступа.", show_alert=True)
            return
        return await func(callback, **kwargs)
    return wrapper

@router.callback_query(F.data == "admin_panel")
@_admin_only
async def show_admin_panel(callback: CallbackQuery):
    try:
        await callback.message.edit_text(
            "🔧 <b>Админ-панель</b>\n\nВыберите действие:",
            parse_mode="HTML",
            reply_markup=get_admin_panel_keyboard()
        )
        await callback.answer()
    except Exception as e:
        logger.error(f"Error in admin_panel: {e}")
        await callback.answer("Error.", show_alert=True)

@router.callback_query(F.data == "admin_stats")
@_admin_only
async def show_admin_stats(callback: CallbackQuery):
    try:
        stats = await db.get_admin_stats()
        
        mode_icons = {"tutor": "🎓", "penfriend": "✉️", "flow": "🎙"}
        mode_lines = ""
        for i, (mode, cnt) in enumerate(stats.get("mode_ranking", []), 1):
            icon = mode_icons.get(mode, "•")
            mode_lines += f"  {i}. {icon} {mode.capitalize()}: <b>{cnt}</b>\n"

        persona_icons = {
            "greg": "🧑‍⚕️", "mark": "👨‍🍳", "junior": "👨‍💻",
            "mrs_smith": "👩‍🏫", "summer": "🌍", "jane": "☕"
        }
        persona_lines = ""
        for i, (persona, cnt) in enumerate(stats.get("top_personas", []), 1):
            icon = persona_icons.get(persona, "👤")
            name = persona.replace("_", " ").capitalize()
            persona_lines += f"  {i}. {icon} {name}: <b>{cnt}</b>\n"

        text = (
            "📊 <b>Статистика</b>\n\n"
            f"👥 Всего: <b>{stats.get('total', 0)}</b>\n"
            f"🆕 Новых сегодня: <b>{stats.get('new_today', 0)}</b>\n"
            f"📅 Новых за неделю: <b>{stats.get('new_week', 0)}</b>\n"
            f"🟢 Активных за неделю: <b>{stats.get('active_week', 0)}</b>\n\n"
            f"🎭 <b>Топ персонажей</b>\n{persona_lines or '  —'}\n"
            f"🗂 <b>Режимы</b>\n{mode_lines or '  —'}"
        )
        await callback.message.edit_text(
            text,
            parse_mode="HTML",
            reply_markup=get_admin_stats_keyboard()
        )
        await callback.answer()
    except Exception as e:
        logger.error(f"Error in admin_stats: {e}")
        await callback.answer("Error loading stats.", show_alert=True)

@router.callback_query(F.data == "admin_users")
@_admin_only
async def show_admin_users(callback: CallbackQuery):
    try:
        users = await db.get_all_users()
        if not users:
            await callback.message.edit_text(
                "👥 Пользователей пока нет.",
                reply_markup=get_admin_panel_keyboard()
            )
            await callback.answer()
            return
        await callback.message.edit_text(
            f"👥 <b>Пользователи</b> ({len(users)})\n\nВыберите для просмотра карточки:",
            parse_mode="HTML",
            reply_markup=get_admin_users_keyboard(users)
        )
        await callback.answer()
    except Exception as e:
        logger.error(f"Error in admin_users: {e}")
        await callback.answer("Error.", show_alert=True)

@router.callback_query(F.data.startswith("admin_user_"))
@_admin_only
async def show_admin_user_card(callback: CallbackQuery):
    try:
        telegram_id = int(callback.data.split("_")[2])
        card = await db.get_user_card(telegram_id)
        if not card:
            await callback.answer("Не удалось загрузить карточку.", show_alert=True)
            return

        user = card.get("user", {})
        name = user.get("username") or str(telegram_id)
        username = f"@{user.get('username')}" if user.get("username") else "—"
        level = str(user.get("level") or "—").upper()
        mode = user.get("mode") or "—"
        persona = (user.get("persona") or "—").replace("_", " ").capitalize()
        streak = user.get("streak_days") or 0
        created = (user.get("created_at") or "")[:10] or "—"
        last_active = (user.get("last_active") or "")[:10] or "—"

        mode_icons = {"tutor": "🎓", "penfriend": "✉️", "flow": "🎙"}
        persona_icons = {
            "greg": "🧑‍⚕️", "mark": "👨‍🍳", "junior": "👨‍💻",
            "mrs smith": "👩‍🏫", "summer": "🌍", "jane": "☕"
        }

        text = (
            f"👤 <b>{html.escape(name)}</b> {html.escape(username)}\n"
            f"🆔 <code>{telegram_id}</code>\n\n"
            f"📚 Уровень: <b>{level}</b>\n"
            f"🗂 Режим: {mode_icons.get(mode, '')} <b>{mode.capitalize()}</b>\n"
            f"🎭 Персонаж: {persona_icons.get(persona.lower(), '👤')} <b>{persona}</b>\n\n"
            f"📅 Зарегистрирован: <b>{created}</b>\n"
            f"🕐 Последняя активность: <b>{last_active}</b>\n"
            f"🔥 Streak: <b>{streak} дн.</b>\n\n"
            f"💬 Сообщений всего: <b>{card.get('msgs_total', 0)}</b>\n"
            f"📈 За неделю: <b>{card.get('msgs_week', 0)}</b>\n\n"
            f"📋 Mistakes Practice: <b>{card.get('active_errors_count', 0)}</b> активных · "
            f"<b>{card.get('mastered_errors_count', 0)}</b> освоено"
        )
        await callback.message.edit_text(
            text,
            parse_mode="HTML",
            reply_markup=get_admin_user_card_keyboard(telegram_id)
        )
        await callback.answer()
    except Exception as e:
        logger.error(f"Error in admin_user_card: {e}")
        await callback.answer("Error.", show_alert=True)

@router.callback_query(F.data == "back_to_settings")
async def back_to_settings(callback: CallbackQuery):
    try:
        user = await db.get_or_create_user(callback.from_user.id)
        notif = user.get("notifications_enabled", True)
        await callback.message.edit_text(
            "⚙️ <b>Settings</b>",
            parse_mode="HTML",
            reply_markup=get_settings_keyboard(notif, callback.from_user.id)
        )
        await callback.answer()
    except Exception as e:
        logger.error(f"Error in back_to_settings: {e}")
        await callback.answer("Error.", show_alert=True)

@router.callback_query(F.data == "admin_broadcast")
@_admin_only
async def start_broadcast(callback: CallbackQuery, state: FSMContext):
    try:
        await state.set_state(AdminState.waiting_broadcast)
        await callback.message.edit_text(
            "📣 <b>Broadcast</b>\n\n"
            "Напишите сообщение — оно будет отправлено всем пользователям.\n\n"
            "<i>Для отмены напишите /cancel</i>",
            parse_mode="HTML",
            reply_markup=None
        )
        await callback.answer()
    except Exception as e:
        logger.error(f"Error in admin_broadcast: {e}")
        await callback.answer("Error.", show_alert=True)

@router.message(AdminState.waiting_broadcast)
async def handle_broadcast_message(message: Message, state: FSMContext):
    if message.from_user.id not in ADMIN_IDS:
        return
    if message.text and message.text.strip() == "/cancel":
        await state.clear()
        await message.answer(
            "🏠 <b>Main Menu</b>",
            parse_mode="HTML",
            reply_markup=get_main_menu_keyboard(message.from_user.id)
        )
        return

    await state.clear()
    user_ids = await db.get_all_user_ids()
    text = message.text or message.caption or ""

    sent_ok = 0
    sent_fail = 0
    for uid in user_ids:
        try:
            await message.bot.send_message(uid, text)
            sent_ok += 1
        except Exception:
            sent_fail += 1
        await asyncio.sleep(0.05)  # FloodWait prevention

    await message.answer(
        f"📣 <b>Broadcast завершён</b>\n\n"
        f"✅ Доставлено: <b>{sent_ok}</b>\n"
        f"❌ Ошибок: <b>{sent_fail}</b>",
        parse_mode="HTML",
        reply_markup=get_admin_panel_keyboard()
    )

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
            send_fn=callback.message.answer,
            edit_msg=lambda text, **kw: callback.message.edit_text(text, **kw)
        )
        await callback.answer()
    except Exception as e:
        logger.error(f"Error in cq_show_stats: {e}")
        await callback.answer("Error loading stats.", show_alert=True)

@router.callback_query(F.data.startswith("stats_deep_"))
async def cq_stats_deep(callback: CallbackQuery):
    try:
        await callback.answer("Generating your report…")
        user_id = callback.from_user.id
        stats = _stats_cache.get(user_id) or await db.get_user_stats(user_id)
        _cache_stats(user_id, stats)

        deep_text = await groq_client.generate_stats_deep_dive(stats)
        safe = html.escape(deep_text)
        msg_id = callback.message.message_id

        await callback.message.edit_text(
            f"📖 <b>Your Progress Report</b>\n\n{safe}",
            parse_mode="HTML",
            reply_markup=get_stats_keyboard(msg_id, "deep")
        )
    except Exception as e:
        logger.error(f"Error in stats_deep: {e}")
        await callback.answer("Error generating report.", show_alert=True)

@router.callback_query(F.data.startswith("stats_quick_"))
async def cq_stats_quick(callback: CallbackQuery):
    try:
        user_id = callback.from_user.id
        stats = _stats_cache.get(user_id) or await db.get_user_stats(user_id)
        _cache_stats(user_id, stats)
        text = _build_quick_stats(stats)
        msg_id = callback.message.message_id

        await callback.message.edit_text(
            text,
            parse_mode="HTML",
            reply_markup=get_stats_keyboard(msg_id, "quick")
        )
        await callback.answer()
    except Exception as e:
        logger.error(f"Error in stats_quick: {e}")
        await callback.answer("Error.", show_alert=True)

@router.callback_query(F.data.startswith("stats_translate_"))
async def cq_stats_translate(callback: CallbackQuery):
    try:
        await callback.answer("Translating…")
        parts = callback.data.split("_")
        mode = parts[-1]
        current_text = re.sub(r"<[^>]+>", "", callback.message.text or "")
        translation = await groq_client.translate_text(current_text)
        safe = html.escape(translation)
        msg_id = callback.message.message_id

        await callback.message.edit_text(
            f"🌐 {safe}",
            parse_mode="HTML",
            reply_markup=get_stats_back_keyboard(msg_id, mode)
        )
    except Exception as e:
        logger.error(f"Error in stats_translate: {e}")
        await callback.answer("Translation failed.", show_alert=True)

@router.callback_query(F.data.startswith("stats_original_"))
async def cq_stats_original(callback: CallbackQuery):
    try:
        parts = callback.data.split("_")
        mode = parts[-1]
        user_id = callback.from_user.id
        stats = _stats_cache.get(user_id) or await db.get_user_stats(user_id)
        _cache_stats(user_id, stats)
        msg_id = callback.message.message_id

        if mode == "deep":
            deep_text = await groq_client.generate_stats_deep_dive(stats)
            safe = html.escape(deep_text)
            text = f"📖 <b>Your Progress Report</b>\n\n{safe}"
        else:
            text = _build_quick_stats(stats)

        await callback.message.edit_text(
            text,
            parse_mode="HTML",
            reply_markup=get_stats_keyboard(msg_id, mode)
        )
        await callback.answer()
    except Exception as e:
        logger.error(f"Error in stats_original: {e}")
        await callback.answer("Error.", show_alert=True)

@router.callback_query(F.data == "my_practice_log")
async def cq_show_practice_log(callback: CallbackQuery):
    try:
        await _send_practice_log(
            callback.from_user.id,
            send_fn=None,
            tab="mistakes",
            edit_msg=lambda text, **kw: callback.message.edit_text(text, **kw)
        )
        await callback.answer()
    except Exception as e:
        logger.error(f"Error in cq_show_practice_log: {e}")
        await callback.answer("Error loading Mistakes Practice.", show_alert=True)

@router.callback_query(F.data.startswith("practice_tab_"))
async def cq_practice_tab(callback: CallbackQuery):
    try:
        tab = callback.data.split("_")[2]
        await _send_practice_log(
            callback.from_user.id,
            send_fn=None,
            tab=tab,
            edit_msg=lambda text, **kw: callback.message.edit_text(text, **kw)
        )
        await callback.answer()
    except Exception as e:
        logger.error(f"Error in practice_tab: {e}")
        await callback.answer("Error.", show_alert=True)

@router.message(Command("practice"))
async def cmd_practice(message: Message):
    await _send_practice_log(message.from_user.id, send_fn=message.answer, tab="mistakes")

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
