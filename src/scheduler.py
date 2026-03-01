import asyncio
import logging
from aiogram import Bot

from src.services.supabase_db import db
from src.services.groq_client import groq_client

logger = logging.getLogger(__name__)

# Интервал проверки — каждый час
CHECK_INTERVAL_SECONDS = 3600


async def send_re_engagement_notifications(bot: Bot) -> None:
    """
    Проверяет пользователей которые не заходили 48+ часов
    и отправляет им персональное уведомление от лица их персонажа.
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

                stats = await db.get_user_stats(telegram_id)
                message_text = await groq_client.generate_re_engagement_notification(
                    persona_key=persona_key,
                    stats=stats
                )

                await bot.send_message(
                    chat_id=telegram_id,
                    text=message_text
                )
                await db.mark_user_notified(telegram_id)
                logger.info(f"✅ Notification sent to user {telegram_id}")

                # Небольшая пауза между отправками чтобы не флудить API
                await asyncio.sleep(0.3)

            except Exception as e:
                logger.error(f"❌ Failed to notify user {user.get('telegram_id')}: {e}")

    except Exception as e:
        logger.error(f"❌ Error in notification scheduler: {e}")


async def run_scheduler(bot: Bot) -> None:
    """
    Фоновая задача: запускается при старте бота,
    проверяет пользователей для уведомлений каждый час.
    """
    logger.info("🕐 Notification scheduler started")
    while True:
        try:
            await asyncio.sleep(CHECK_INTERVAL_SECONDS)
            await send_re_engagement_notifications(bot)
        except asyncio.CancelledError:
            logger.info("🛑 Notification scheduler stopped")
            break
        except Exception as e:
            logger.error(f"❌ Scheduler error: {e}")
            await asyncio.sleep(60)
