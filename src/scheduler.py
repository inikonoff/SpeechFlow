import asyncio
import logging
from datetime import datetime, timezone
from aiogram import Bot
from aiogram.types import BufferedInputFile
from src.bot.keyboards import get_flow_voice_keyboard

from src.services.supabase_db import db
from src.services.groq_client import groq_client
from src.personas import get_persona_voice, get_persona_display

logger = logging.getLogger(__name__)

CHECK_INTERVAL_SECONDS = 3600  # проверка каждый час


async def send_sunday_deep_dive(bot: Bot) -> None:
    """
    Воскресный Deep Dive от Mrs. Smith.
    Отправляется всем пользователям с включёнными уведомлениями
    каждое воскресенье между 9:00 и 10:00 UTC.
    Содержит анализ ошибок за неделю с реальными примерами.
    """
    try:
        users = await db.get_users_for_sunday_report()
        if not users:
            return

        logger.info(f"📊 Sending Sunday Deep Dive to {len(users)} users")

        for user in users:
            try:
                telegram_id = user.get("telegram_id")

                stats = await db.get_user_stats(telegram_id)
                errors = await db.get_weekly_errors_for_report(telegram_id)

                report_text = await groq_client.generate_sunday_deep_dive(
                    stats=stats,
                    errors=errors
                )

                await bot.send_message(
                    chat_id=telegram_id,
                    text=f"📚 <b>Your Weekly Deep Dive</b>\n\n{report_text}",
                    parse_mode="HTML"
                )

                logger.info(f"✅ Sunday Deep Dive sent to user {telegram_id}")
                await asyncio.sleep(0.5)

            except Exception as e:
                logger.error(f"❌ Failed to send Deep Dive to {user.get('telegram_id')}: {e}")

    except Exception as e:
        logger.error(f"❌ Error in sunday deep dive: {e}")


async def send_re_engagement_notifications(bot: Bot) -> None:
    """
    Персональное голосовое уведомление от персонажа юзера
    для тех, кто не заходил 23.5+ часов.
    """
    try:
        users = await db.get_users_for_notification()
        if not users:
            return

        logger.info(f"📬 Sending re-engagement notifications to {len(users)} users")

        for user in users:
            try:
                telegram_id = user.get("telegram_id")
                persona_key = user.get("persona", "greg")
                voice = get_persona_voice(persona_key)
                persona_display = get_persona_display(persona_key)

                stats = await db.get_user_stats(telegram_id)

                attempt = user.get("reengagement_count", 0) or 0
                message_text = await groq_client.generate_re_engagement_notification(
                    persona_key=persona_key,
                    stats=stats,
                    attempt=attempt,
                )

                voice_bytes = await groq_client.text_to_speech(message_text, voice=voice)

                if voice_bytes:
                    voice_file = BufferedInputFile(voice_bytes, filename="re_engagement.wav")
                    sent = await bot.send_voice(
                        chat_id=telegram_id,
                        voice=voice_file,
                        caption=f"🎙 {persona_display}",
                        reply_markup=get_flow_voice_keyboard(0)
                    )
                    await bot.edit_message_reply_markup(
                        chat_id=telegram_id,
                        message_id=sent.message_id,
                        reply_markup=get_flow_voice_keyboard(sent.message_id)
                    )
                else:
                    await bot.send_message(
                        chat_id=telegram_id,
                        text=f"💬 <b>{persona_display}</b>\n\n{message_text}",
                        parse_mode="HTML"
                    )

                await db.mark_user_notified(telegram_id)
                logger.info(f"✅ Re-engagement sent to user {telegram_id}")
                await asyncio.sleep(1.5)

            except Exception as e:
                logger.error(f"❌ Failed to notify user {user.get('telegram_id')}: {e}")

    except Exception as e:
        logger.error(f"❌ Error in re-engagement scheduler: {e}")


_last_sunday_deep_dive_week: str = ""  # ISO-неделя ("2026-W35") последней успешной рассылки


async def run_scheduler(bot: Bot) -> None:
    """
    Фоновая задача: каждый час проверяет:
    1. Воскресенье, начиная с 9:00 UTC → Sunday Deep Dive от Mrs. Smith
    2. Каждый час → re-engagement для неактивных юзеров

    Окно для Sunday Deep Dive расширено до "9:00–12:00 UTC" вместо строго
    одного часа: если процесс перезапустился (деплой на Render) и не
    успел тикнуть ровно в час 9, рассылка не потеряется на всю неделю —
    следующий тик в пределах того же окна её досошлёт. Флаг "уже
    отправляли на этой ISO-неделе" в памяти процесса не даёт отправить
    дважды за одно и то же окно.

    ВАЖНО: флаг живёт только в памяти процесса — если контейнер
    перезапустится ПОСЛЕ уже состоявшейся отправки, но всё ещё внутри
    окна 9:00–12:00, рассылка в теории может уйти повторно. Полностью
    убрать этот риск можно только персистентной отметкой в БД (отдельная
    колонка/таблица в Supabase) — этого в текущей схеме нет, добавлять
    вслепую без доступа к консоли Supabase не стал.
    """
    global _last_sunday_deep_dive_week
    logger.info("🕐 Scheduler started")
    while True:
        try:
            await asyncio.sleep(CHECK_INTERVAL_SECONDS)

            now = datetime.now(timezone.utc)
            current_week = now.strftime("%G-W%V")

            # Sunday Deep Dive: воскресенье (weekday=6), окно 9:00–12:00 UTC,
            # максимум раз в ISO-неделю.
            if (
                now.weekday() == 6
                and 9 <= now.hour < 12
                and _last_sunday_deep_dive_week != current_week
            ):
                await send_sunday_deep_dive(bot)
                _last_sunday_deep_dive_week = current_week

            # Re-engagement: каждый час
            await send_re_engagement_notifications(bot)

        except asyncio.CancelledError:
            logger.info("🛑 Scheduler stopped")
            break
        except Exception as e:
            logger.error(f"❌ Scheduler error: {e}")
            await asyncio.sleep(60)
