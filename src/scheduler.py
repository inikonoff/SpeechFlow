import asyncio
import logging
from aiogram import Bot
from aiogram.types import BufferedInputFile

from src.services.supabase_db import db
from src.services.groq_client import groq_client
from src.personas import get_persona_voice, get_persona_display

logger = logging.getLogger(__name__)

# Интервал проверки — каждый час
CHECK_INTERVAL_SECONDS = 3600


async def send_re_engagement_notifications(bot: Bot) -> None:
    """
    Проверяет пользователей которые не заходили 48+ часов
    и отправляет им персональное голосовое уведомление от лица их персонажа.
    """
    try:
        users = await db.get_users_for_notification()
        if not users:
            return

        logger.info(f"📬 Sending voice re-engagement notifications to {len(users)} users")

        for user in users:
            try:
                telegram_id = user.get("telegram_id")
                persona_key = user.get("persona", "greg")
                
                # Получаем голос и красивое имя персонажа (например: 👨‍🍳 Mark)
                voice = get_persona_voice(persona_key)
                persona_display = get_persona_display(persona_key)

                stats = await db.get_user_stats(telegram_id)
                
                # Генерируем уникальный текст
                message_text = await groq_client.generate_re_engagement_notification(
                    persona_key=persona_key,
                    stats=stats
                )

                # Пробуем сгенерировать голосовое сообщение
                voice_bytes = await groq_client.text_to_speech(message_text, voice=voice)

                if voice_bytes:
                    # Отправляем аудио с короткой подписью
                    voice_file = BufferedInputFile(voice_bytes, filename="re_engagement.wav")
                    await bot.send_voice(
                        chat_id=telegram_id,
                        voice=voice_file,
                        caption=f"🎙 {persona_display}"
                    )
                else:
                    # Fallback: если TTS недоступен или упал с ошибкой, отправляем текст
                    await bot.send_message(
                        chat_id=telegram_id,
                        text=f"💬 <b>{persona_display}</b>\n\n{message_text}",
                        parse_mode="HTML"
                    )
                
                # Отмечаем пользователя как уведомленного, чтобы не спамить его каждый час
                await db.mark_user_notified(telegram_id)
                logger.info(f"✅ Notification sent to user {telegram_id}")

                # Пауза между отправками увеличена, так как TTS - тяжелый API-запрос
                await asyncio.sleep(1.5)

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
