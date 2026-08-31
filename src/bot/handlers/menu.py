# CHANGELOG: 2026-07-16
# - get_settings_keyboard: все вызовы обновлены с synonym_streak параметром
# - toggle_synonym_streak: новый хендлер
# - Paywall: upgrade, paywall_plan_*, paywall_buy_*, pre_checkout, successful_payment
# - Импорты: get_paywall_keyboard, get_paywall_period_keyboard, get_paywall_success_keyboard

import logging
import html
import re
import asyncio
from typing import Dict
from aiogram import Router, F
from aiogram.types import Message, CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
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
    get_paywall_period_keyboard,
    get_paywall_success_keyboard,
)
from src.personas import get_all_personas
from src.services.supabase_db import db
from src.services.groq_client import groq_client
from src.utils.tg_helpers import safe_edit_text, safe_edit_reply_markup
from src.config import ADMIN_IDS

router = Router()
logger = logging.getLogger(__name__)

_how_to_originals: Dict[int, str] = {}

HOW_TO_TEXT = """🗣 <b>Speech Flow Pro — How it works</b>

<b>🎓 Tutor Mode</b>
Speak or write in English. Mrs. Smith replies naturally — and quietly sends you a correction card if she spots a real mistake. Two separate messages, zero interruptions to the conversation.

<b>✉️ PenFriend Mode</b>
Text chat with one of six characters. Turn on <b>Recasting Mode</b> in Settings and your partner will naturally weave the correct form of your mistake into their reply — highlighted in bold. No lectures, just a nudge.

<b>🎙 Flow Mode</b>
Pure voice. No corrections, no text overlays — just talk. Every Sunday, Mrs. Smith sends you a personal Deep Dive report with your most frequent patterns and real examples from your conversations.

<b>👥 Characters</b>
Greg 🏥 · Mark 🍳 · Junior 💻 · Mrs. Smith 📚 · Summer 🌍 · Jane ☕
Six people, one small world. Each remembers your conversations.

<b>⚙️ Settings</b>
Change your English level · Toggle <b>Recasting Mode</b> for PenFriend soft corrections · Manage notifications."""


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
    await _send_stats(message, message.from_user.id, edit=False)

@router.message(Command("settings"))
async def cmd_settings(message: Message):
    user = await db.get_or_create_user(message.from_user.id)
    notif     = user.get("notifications_enabled", True)
    recasting = user.get("recasting_enabled", False)
    practice  = user.get("mistakes_practice_enabled", False)
    plan      = user.get("subscription_plan") or "free"
    await message.answer(
        "⚙️ <b>Settings</b>",
        parse_mode="HTML",
        reply_markup=get_settings_keyboard(notif, recasting, practice, message.from_user.id, plan)
    )

@router.message(Command("support"))
async def cmd_support(message: Message):
    await message.answer(
        "👨‍💻 <b>Speech Flow Pro</b>\n\n"
        "Created by: @inikonoff\n\n"
        "Questions, feedback, or bug reports — write directly to @inikonoff.",
        parse_mode="HTML"
    )

@router.message(Command("author"))
async def cmd_author(message: Message):
    """Алиас для обратной совместимости."""
    await cmd_support(message)

# ─── Stats cache ────────────────────────────────────────────────────────────

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

    text = (
        f"📊 <b>Your Speech Flow Stats</b>\n\n"
        f"👤 <b>Profile</b>\n"
        f"• Level: <b>{level}</b>\n"
        f"• Partner: <b>{persona}</b>\n"
        f"{streak_line}\n\n"
        f"📈 <b>This week</b>\n"
        f"{activity_line}\n\n"
        f"🎯 <b>Errors logged this week</b>\n"
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

    # Блок Mistakes Practice
    active = stats.get("active_errors_count", 0)
    mastered = stats.get("mastered_errors_count", 0)
    if active or mastered:
        text += f"\n\n📋 <b>Mistakes Practice</b>\n"
        text += f"• Active: <b>{active}</b>  ✅ Mastered: <b>{mastered}</b>"

    return text

def get_stats_keyboard(message_id: int, mode: str = "quick") -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    if mode == "quick":
        builder.row(InlineKeyboardButton(text="📖 Deep Dive", callback_data=f"stats_deep_{message_id}"))
    else:
        builder.row(InlineKeyboardButton(text="📊 Quick Stats", callback_data=f"stats_quick_{message_id}"))
    builder.row(InlineKeyboardButton(text="📋 My Mistakes", callback_data="my_practice_log"))
    builder.row(InlineKeyboardButton(text="🌐 Translate", callback_data=f"stats_translate_{message_id}_{mode}"))
    return builder.as_markup()

async def _send_stats(message, user_id: int, edit: bool = False):
    try:
        stats = await db.get_user_stats(user_id)
        _cache_stats(user_id, stats)
        text = _build_quick_stats(stats)

        if edit:
            sent = await safe_edit_text(message, text, parse_mode="HTML", reply_markup=None)
        else:
            sent = await message.answer(text, parse_mode="HTML")

        await safe_edit_reply_markup(sent, reply_markup=get_stats_keyboard(sent.message_id, "quick"))
    except Exception as e:
        logger.error(f"Error in stats: {e}")
        err_text = "Error loading stats."
        if edit:
            await safe_edit_text(message, err_text, parse_mode="HTML")
        else:
            await message.answer(err_text, parse_mode="HTML")

# ─── Callbacks ─────────────────────────────────────────────────────────────

@router.callback_query(F.data == "how_to_use")
async def show_how_to_use(callback: CallbackQuery):
    try:
        from aiogram.utils.keyboard import InlineKeyboardBuilder
        from aiogram.types import InlineKeyboardButton
        sent = await safe_edit_text(callback.message, HOW_TO_TEXT, parse_mode="HTML")
        if len(_how_to_originals) > 1000:
            _how_to_originals.clear()
        _how_to_originals[sent.message_id] = HOW_TO_TEXT
        builder = InlineKeyboardBuilder()
        builder.row(InlineKeyboardButton(text="🌐 Translate", callback_data=f"howto_translate_{sent.message_id}"))
        await safe_edit_reply_markup(sent, reply_markup=builder.as_markup())
        await callback.answer()
    except Exception as e:
        logger.error(f"Error showing how to use: {e}")
        await callback.answer("Error.", show_alert=True)

@router.callback_query(F.data == "settings")
async def show_settings(callback: CallbackQuery):
    try:
        user = await db.get_or_create_user(callback.from_user.id)
        notif     = user.get("notifications_enabled", True)
        recasting = user.get("recasting_enabled", False)
        practice  = user.get("mistakes_practice_enabled", False)
        plan      = user.get("subscription_plan") or "free"
        await safe_edit_text(
            callback.message,
            "⚙️ <b>Settings</b>",
            parse_mode="HTML",
            reply_markup=get_settings_keyboard(notif, recasting, practice, callback.from_user.id, plan)
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

        recasting = user.get("recasting_enabled", False)
        practice  = user.get("mistakes_practice_enabled", False)
        plan      = user.get("subscription_plan") or "free"
        await safe_edit_reply_markup(
            callback.message,
            reply_markup=get_settings_keyboard(new_value, recasting, practice, callback.from_user.id, plan)
        )
    except Exception as e:
        logger.error(f"Error toggling notifications: {e}")
        await callback.answer("Error.", show_alert=True)

@router.callback_query(F.data == "toggle_recasting")
async def cq_toggle_recasting(callback: CallbackQuery):
    try:
        new_val = await db.toggle_recasting(callback.from_user.id)
        status = "ON 📝" if new_val else "OFF 📝"
        await callback.answer(f"Recasting Mode {status}", show_alert=False)

        user = await db.get_or_create_user(callback.from_user.id)
        notif = user.get("notifications_enabled", True)
        practice  = user.get("mistakes_practice_enabled", False)
        plan      = user.get("subscription_plan") or "free"
        await safe_edit_reply_markup(
            callback.message,
            reply_markup=get_settings_keyboard(notif, new_val, practice, callback.from_user.id, plan)
        )
    except Exception as e:
        logger.error(f"Error toggling recasting: {e}")
        await callback.answer("Error.", show_alert=True)

@router.callback_query(F.data == "toggle_mistakes_practice")
async def cq_toggle_mistakes_practice(callback: CallbackQuery):
    try:
        new_val = await db.toggle_mistakes_practice(callback.from_user.id)
        status = "ON 🎯" if new_val else "OFF 🎯"
        await callback.answer(f"Mistakes Practice {status}", show_alert=False)
        user = await db.get_or_create_user(callback.from_user.id)
        notif     = user.get("notifications_enabled", True)
        recasting = user.get("recasting_enabled", False)
        plan      = user.get("subscription_plan") or "free"
        await safe_edit_reply_markup(
            callback.message,
            reply_markup=get_settings_keyboard(notif, recasting, new_val, callback.from_user.id, plan)
        )
    except Exception as e:
        logger.error(f"Error toggling mistakes practice: {e}")
        await callback.answer("Error.", show_alert=True)

@router.callback_query(F.data == "admin_cycle_plan")
async def cq_admin_cycle_plan(callback: CallbackQuery):
    """Только для ADMIN_IDS: циклический переключатель тарифа для тестирования платных фич."""
    try:
        if callback.from_user.id not in ADMIN_IDS:
            await callback.answer("⛔ Admin only.", show_alert=True)
            return

        user = await db.get_or_create_user(callback.from_user.id)
        current_plan = user.get("subscription_plan") or "free"
        cycle = {"free": "pro", "pro": "free"}
        new_plan = cycle.get(current_plan, "free")

        # expires_at сбрасываем в None — иначе check_subscription_expired
        # откатит тестовый план обратно на free при следующей проверке лимита.
        await db.update_user(callback.from_user.id, {
            "subscription_plan": new_plan,
            "subscription_expires_at": None,
        })
        await callback.answer(f"Test plan → {new_plan.capitalize()}", show_alert=False)

        notif     = user.get("notifications_enabled", True)
        recasting = user.get("recasting_enabled", False)
        practice  = user.get("mistakes_practice_enabled", False)
        await safe_edit_reply_markup(
            callback.message,
            reply_markup=get_settings_keyboard(notif, recasting, practice, callback.from_user.id, new_plan)
        )
    except Exception as e:
        logger.error(f"Error cycling admin test plan: {e}")
        await callback.answer("Error.", show_alert=True)

# ─── Paywall ──────────────────────────────────────────────────────────────────

@router.callback_query(F.data == "upgrade")
async def cq_upgrade(callback: CallbackQuery):
    """Кнопка 'Upgrade' из любого места — показывает выбор тарифа."""
    try:
        await safe_edit_text(
            callback.message,
            "⭐ <b>Upgrade to Speech Flow Pro</b>\n\n"
            "• Unlimited messages\n"
            "• All 6 characters\n"
            "• Recasting + Session Summary\n"
            "• Synonym Streak + Drop-in Talks",
            parse_mode="HTML",
            reply_markup=get_paywall_period_keyboard()
        )
        await callback.answer()
    except Exception as e:
        logger.error(f"Error in upgrade: {e}")
        await callback.answer("Error.", show_alert=True)

@router.callback_query(F.data.startswith("paywall_buy_"))
async def cq_paywall_buy(callback: CallbackQuery):
    """Инициирует платёж через Telegram Stars."""
    try:
        from src.config import STARS_PRICES
        parts = callback.data.split("_")  # paywall_buy_pro_2weeks
        plan   = parts[2]
        period = parts[3]
        prices = STARS_PRICES.get(plan, {})
        amount = prices.get(period, 0)
        if not amount:
            await callback.answer("Price not found.", show_alert=True)
            return

        period_names = {"2weeks": "2 weeks", "month": "1 month"}

        await callback.message.answer_invoice(
            title="Speech Flow Pro",
            description=f"{period_names.get(period, period)} subscription to Speech Flow Pro",
            payload=f"sub_{plan}_{period}",
            currency="XTR",           # Telegram Stars
            prices=[{"label": f"Pro — {period_names.get(period, period)}", "amount": amount}],
            provider_token="",         # пустой для Stars
        )
        await callback.answer()
    except Exception as e:
        logger.error(f"Error in paywall buy: {e}")
        await callback.answer("Payment error. Try again.", show_alert=True)

@router.pre_checkout_query()
async def pre_checkout(query):
    """Обязательный хендлер — подтверждаем оплату."""
    await query.answer(ok=True)

@router.message(F.successful_payment)
async def successful_payment(message: Message):
    """Пользователь оплатил — активируем подписку."""
    try:
        payload = message.successful_payment.invoice_payload  # sub_pro_2weeks
        parts = payload.split("_")
        plan   = parts[1]
        period = parts[2]
        await db.activate_subscription(message.from_user.id, plan, period)
        period_names = {"2weeks": "2 weeks", "month": "1 month"}
        await message.answer(
            f"🎉 <b>Welcome to Pro!</b>\n\n"
            f"Your subscription is active for {period_names.get(period, period)}.\n"
            f"Enjoy unlimited conversations!",
            parse_mode="HTML",
            reply_markup=get_paywall_success_keyboard()
        )
    except Exception as e:
        logger.error(f"Error in successful_payment: {e}")

@router.callback_query(F.data == "back_to_menu")
async def back_to_menu(callback: CallbackQuery):
    try:
        await safe_edit_text(
            callback.message,
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
        await safe_edit_text(
            callback.message,
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
            "greg": "🏥", "mark": "🍳", "junior": "💻",
            "mrs_smith": "📚", "summer": "🌍", "jane": "☕"
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
        await safe_edit_text(
            callback.message, text,
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
            await safe_edit_text(
                callback.message,
                "👥 Пользователей пока нет.",
                reply_markup=get_admin_panel_keyboard()
            )
            await callback.answer()
            return
        await safe_edit_text(
            callback.message,
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
            "greg": "🏥", "mark": "🍳", "junior": "💻",
            "mrs smith": "📚", "summer": "🌍", "jane": "☕"
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
            f"📈 За неделю: <b>{card.get('msgs_week', 0)}</b>"
        )
        await safe_edit_text(
            callback.message, text,
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
        notif     = user.get("notifications_enabled", True)
        recasting = user.get("recasting_enabled", False)
        practice  = user.get("mistakes_practice_enabled", False)
        plan      = user.get("subscription_plan") or "free"
        await safe_edit_text(
            callback.message,
            "⚙️ <b>Settings</b>",
            parse_mode="HTML",
            reply_markup=get_settings_keyboard(notif, recasting, practice, callback.from_user.id, plan)
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
        await safe_edit_text(
            callback.message,
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
            # Отправляем сообщение и добавляем кнопку перевода
            sent = await message.bot.send_message(uid, text)
            await message.bot.edit_message_reply_markup(
                chat_id=uid,
                message_id=sent.message_id,
                reply_markup=get_translate_keyboard(sent.message_id)
            )
            sent_ok += 1
        except Exception:
            sent_fail += 1
        await asyncio.sleep(0.05)

    await message.answer(
        f"📣 <b>Broadcast завершён</b>\n\n"
        f"✅ Доставлено: <b>{sent_ok}</b>\n"
        f"❌ Ошибок: <b>{sent_fail}</b>",
        parse_mode="HTML",
        reply_markup=get_admin_panel_keyboard()
    )

@router.callback_query(F.data == "my_stats")
async def cq_show_stats(callback: CallbackQuery):
    try:
        await _send_stats(callback.message, callback.from_user.id, edit=True)
        await callback.answer()
    except Exception as e:
        logger.error(f"Error in cq_show_stats: {e}")
        await callback.answer("Error loading stats.", show_alert=True)

@router.callback_query(F.data.startswith("stats_deep_"))
async def cq_stats_deep(callback: CallbackQuery):
    try:
        await callback.answer("Mrs. Smith is reviewing your progress…")
        user_id = callback.from_user.id
        stats = _stats_cache.get(user_id) or await db.get_user_stats(user_id)
        _cache_stats(user_id, stats)

        # Добавляем ошибки за неделю для более глубокого анализа
        errors = await db.get_weekly_errors_for_report(user_id)

        # Если есть ошибки — используем sunday deep dive формат (с примерами).
        # Он уже возвращает готовый HTML (<blockquote>/<b>), пользовательский
        # текст экранирован внутри generate_sunday_deep_dive — повторный
        # html.escape() здесь сломал бы разметку.
        # Если ошибок нет — используем stats deep dive (чистая проза без
        # HTML-тегов), её как раз нужно экранировать перед рендером.
        if errors:
            deep_text = await groq_client.generate_sunday_deep_dive(stats, errors)
            safe = deep_text
        else:
            deep_text = await groq_client.generate_stats_deep_dive(stats)
            safe = html.escape(deep_text)
        msg_id = callback.message.message_id

        await safe_edit_text(
            callback.message,
            f"📚 <b>Mrs. Smith's Note</b>\n\n{safe}",
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

        await safe_edit_text(
            callback.message, text,
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

        await safe_edit_text(
            callback.message,
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
            errors = await db.get_weekly_errors_for_report(user_id)
            if errors:
                deep_text = await groq_client.generate_sunday_deep_dive(stats, errors)
                safe = deep_text  # уже готовый HTML, экранировано внутри groq_client
            else:
                deep_text = await groq_client.generate_stats_deep_dive(stats)
                safe = html.escape(deep_text)  # чистая проза без HTML-тегов
            text = f"📚 <b>Mrs. Smith's Note</b>\n\n{safe}"
        else:
            text = _build_quick_stats(stats)

        await safe_edit_text(
            callback.message, text,
            parse_mode="HTML",
            reply_markup=get_stats_keyboard(msg_id, mode)
        )
        await callback.answer()
    except Exception as e:
        logger.error(f"Error in stats_original: {e}")
        await callback.answer("Error.", show_alert=True)

@router.callback_query(F.data.startswith("howto_translate_"))
async def handle_howto_translate(callback: CallbackQuery):
    """Перевод How To Use."""
    try:
        message_id = int(callback.data.split("_")[2])
        original = _how_to_originals.get(message_id, HOW_TO_TEXT)
        clean_text = re.sub(r'<[^>]+>', '', original)
        translation = await groq_client.translate_text(clean_text)
        safe_translation = html.escape(translation)
        from aiogram.utils.keyboard import InlineKeyboardBuilder
        from aiogram.types import InlineKeyboardButton
        builder = InlineKeyboardBuilder()
        builder.row(InlineKeyboardButton(text="🔤 Original", callback_data=f"howto_original_{message_id}"))
        await safe_edit_text(
            callback.message,
            f"🌐 {safe_translation}",
            parse_mode="HTML",
            reply_markup=builder.as_markup()
        )
        await callback.answer()
    except Exception as e:
        logger.error(f"Error translating how to use: {e}")
        await callback.answer("Translation failed.", show_alert=True)

@router.callback_query(F.data.startswith("howto_original_"))
async def handle_howto_original(callback: CallbackQuery):
    """Возврат к оригиналу How To Use."""
    try:
        message_id = int(callback.data.split("_")[2])
        from aiogram.utils.keyboard import InlineKeyboardBuilder
        from aiogram.types import InlineKeyboardButton
        builder = InlineKeyboardBuilder()
        builder.row(InlineKeyboardButton(text="🌐 Translate", callback_data=f"howto_translate_{message_id}"))
        await safe_edit_text(
            callback.message,
            HOW_TO_TEXT,
            parse_mode="HTML",
            reply_markup=builder.as_markup()
        )
        await callback.answer()
    except Exception as e:
        logger.error(f"Error restoring how to use: {e}")
        await callback.answer("Could not restore original.", show_alert=True)
