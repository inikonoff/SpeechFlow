import logging
from datetime import datetime, timezone, timedelta
from typing import Callable, Dict, Any, Awaitable, Optional

from aiogram import BaseMiddleware
from aiogram.types import TelegramObject, Message

from src.services.supabase_db import db
from src.config import ADMIN_IDS

logger = logging.getLogger(__name__)

# ─── Константы защиты ─────────────────────────────────────────────────────

MAX_MESSAGE_LENGTH   = 2000    # символов — больше обрезаем
RATE_LIMIT_WINDOW    = 60      # секунд для подсчёта сообщений
RATE_LIMIT_MAX       = 10      # макс сообщений за WINDOW для обычного юзера
RATE_LIMIT_MAX_ADMIN = 100     # мягкий лимит для админа
COOLDOWN_DURATION    = 60      # секунд тишины после превышения лимита
WARN_THRESHOLD       = 7       # с этого количества предупреждаем

# Состояние (окно/счётчик/cooldown) хранится в таблице users
# (burst_message_count/burst_window_start/burst_cooldown_until) — раньше
# было в in-memory словарях этого модуля и обнулялось на каждом рестарте
# процесса, то есть спамер получал новый чистый лимит просто дождавшись
# редеплоя. Теперь переживает рестарт так же, как daily_messages_used.


def _parse_dt(value: Optional[str]) -> Optional[datetime]:
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except Exception:
        return None


class UserMiddleware(BaseMiddleware):
    async def __call__(
        self,
        handler: Callable[[TelegramObject, Dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: Dict[str, Any]
    ) -> Any:

        user_id = None
        if hasattr(event, 'from_user') and event.from_user:
            user_id = event.from_user.id
        elif hasattr(event, 'message') and event.message and event.message.from_user:
            user_id = event.message.from_user.id
        elif hasattr(event, 'callback_query') and event.callback_query:
            user_id = event.callback_query.from_user.id

        if not user_id:
            return await handler(event, data)

        is_admin = user_id in ADMIN_IDS

        try:
            user = await db.get_or_create_user(user_id)
            data["user"] = user
            data["is_admin"] = is_admin
        except Exception as e:
            logger.error(f"Error loading user {user_id}: {e}")
            data["user"] = {}
            data["is_admin"] = False
            user = {}

        message: Message | None = None
        if isinstance(event, Message):
            message = event
        elif hasattr(event, 'message') and isinstance(event.message, Message):
            message = event.message

        if message is None:
            return await handler(event, data)

        now = datetime.now(timezone.utc)
        limit = RATE_LIMIT_MAX_ADMIN if is_admin else RATE_LIMIT_MAX

        cooldown_until = _parse_dt(user.get("burst_cooldown_until"))
        if not is_admin and cooldown_until and now < cooldown_until:
            # Предупреждение уже было отправлено в момент входа в cooldown
            # (см. ниже) — дальше молча дропаем, чтобы не спамить "подождите"
            # в ответ на и без того частые сообщения.
            remaining = int((cooldown_until - now).total_seconds())
            logger.warning(f"User {user_id} in cooldown, {remaining}s left")
            return

        window_start = _parse_dt(user.get("burst_window_start"))
        count = user.get("burst_message_count", 0) or 0

        if not window_start or (now - window_start).total_seconds() > RATE_LIMIT_WINDOW:
            window_start = now
            count = 0

        count += 1

        if count > limit and not is_admin:
            new_cooldown = now + timedelta(seconds=COOLDOWN_DURATION)
            await db.update_user(user_id, {
                "burst_message_count": count,
                "burst_window_start": window_start.isoformat(),
                "burst_cooldown_until": new_cooldown.isoformat(),
            })
            logger.warning(f"Rate limit: user {user_id}, {count} msgs/{RATE_LIMIT_WINDOW}s")
            await message.answer(
                f"You've sent {count} messages in under a minute. "
                f"Take a breath — I'll be back in {COOLDOWN_DURATION} seconds. ☕"
            )
            return

        if count == WARN_THRESHOLD and not is_admin:
            await message.answer(
                "You're sending messages very fast — slow down a little so I can keep up."
            )

        await db.update_user(user_id, {
            "burst_message_count": count,
            "burst_window_start": window_start.isoformat(),
            "burst_cooldown_until": None,
        })

        if message.text and len(message.text) > MAX_MESSAGE_LENGTH:
            original_len = len(message.text)
            message.text = message.text[:MAX_MESSAGE_LENGTH] + " [...]"
            logger.info(f"Message from {user_id} truncated: {original_len} → {MAX_MESSAGE_LENGTH} chars")

        return await handler(event, data)
