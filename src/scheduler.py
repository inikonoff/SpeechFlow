import asyncio
import logging
from datetime import datetime, timezone
from aiogram import Bot
from aiogram.types import BufferedInputFile

from src.services.supabase_db import db
from src.services.groq_client import groq_client
from src.personas import get_persona_voice, get_persona_display

logger = logging.getLogger(__name__)

CHECK_INTERVAL_SECONDS = 3600

async def send_re_engagement_notifications(bot: Bot) -> None:
    # ... (Оставьте вашу текущую функцию send_re_engagement_notifications без изменений) ...
    pass # Вставьте сюда ваш код функции send_re_engagement_notifications из оригинального файла

async def send_weekly_deep_dive_reports(bot: Bot) -> None:
    """
    Воскресенье 19:00–20:00 UTC — Deep Dive отчёт для пользователей
    у которых накопились Flow-ошибки за неделю.
    """
    try:
        users = await db.get_flow_users_for_weekly_report()
        if not users:
            logger.info("📊 Deep Dive: нет пользователей для отчёта")
            return

        logger.info(f"📊 Sending Deep Dive reports to {len(users)} users")

        for user in users:
            try:
                telegram_id = user.get("telegram_id")
                
                errors = await db.get_flow_errors_for_report(telegram_id)
                if not errors:
                    continue

                # Строим текст для промпта
                errors_text = "\n".join([
                    f"{i+1}. [{e['category']}] You said: \"{e['original']}\" -> Correct: \"{e['corrected']}\""
                    for i, e in enumerate(errors)
                ])

                report_prompt = (
                    f"You are generating a Weekly Deep Dive report for an English learner. "
                    f"They've been using Flow Mode (voice-only, no corrections during conversation). "
                    f"Here are their top recurring error patterns from this week:\n\n{errors_text}\n\n"
                    f"Write a warm, motivating report in Russian. Structure:\n"
                    f"1. Short encouraging opening (1 sentence)\n"
                    f"2. For each error: show ❌ what they said and ✅ how it sounds naturally, "
                    f"with a brief Russian explanation (1 sentence)\n"
                    f"3. Closing: simple encouragement.\n"
                    f"Keep it under 200 words. No lecture tone — supportive and specific."
                )

                report_text = await groq_client.generate_simple_text(report_prompt)
                if not report_text:
                    continue

                await bot.send_message(
                    chat_id=telegram_id,
                    text=f"💎 <b>Your Weekly Deep Dive</b>\n\n{report_text}",
                    parse_mode="HTML"
                )

                await db.mark_weekly_report_sent(telegram_id)
                logger.info(f"✅ Deep Dive report sent to user {telegram_id}")
                await asyncio.sleep(2.0)

            except Exception as e:
                logger.error(f"❌ Failed to send Deep Dive to {user.get('telegram_id')}: {e}")

    except Exception as e:
        logger.error(f"❌ Error in Deep Dive scheduler: {e}")

async def run_scheduler(bot: Bot) -> None:
    logger.info("🕐 Notification scheduler started")
    while True:
        try:
            await asyncio.sleep(CHECK_INTERVAL_SECONDS)
            
            # Проверка ре-ингейджмента
            await send_re_engagement_notifications(bot)

            # Воскресный Deep Dive — запускаем в окне 19:00–20:00 UTC
            now = datetime.now(timezone.utc)
            if now.weekday() == 6 and 19 <= now.hour < 20:
                await send_weekly_deep_dive_reports(bot)

        except asyncio.CancelledError:
            logger.info("🛑 Notification scheduler stopped")
            break
        except Exception as e:
            logger.error(f"❌ Scheduler error: {e}")
            await asyncio.sleep(60)
