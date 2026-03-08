import time
import logging
from collections import defaultdict
from typing import Callable, Dict, Any, Awaitable

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

# ─── In-memory хранилище ──────────────────────────────────────────────────
_message_timestamps: Dict[int, list] = defaultdict(list)
_cooldowns: Dict[int, float] = {}
_warned: Dict[int, bool] = {}


def _clean_old(user_id: int, now: float) -> None:
    cutoff = now - RATE_LIMIT_WINDOW
    _message_timestamps[user_id] = [
        t for t in _message_timestamps[user_id] if t > cutoff
    ]


def _count_recent(user_id: int, now: float) -> int:
    _clean_old(user_id, now)
    return len(_message_timestamps[user_id])


class UserMiddleware(BaseMiddleware):
    """
    Загрузка пользователя + защита от спама.
    - Rate limit: 20 сообщений / 60 сек → cooldown 60 сек
    - Предупреждение при 15+ сообщениях
    - Обрезка текста до 2000 символов
    - Админы: мягкий лимит 100, без cooldown
    """

    async def __call__(
        self,
        handler: Callable[[TelegramObject, Dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: Dict[str, Any]
    ) -> Any:

        # ── Извлекаем user_id ─────────────────────────────────────────────
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

        # ── Загружаем пользователя ────────────────────────────────────────
        try:
            user = await db.get_or_create_user(user_id)
            data["user"] = user
            data["is_admin"] = is_admin
        except Exception as e:
            logger.error(f"Error loading user {user_id}: {e}")
            data["user"] = {}
            data["is_admin"] = False

        # ── Защита только для Message ─────────────────────────────────────
        message: Message | None = None
        if isinstance(event, Message):
            message = event
        elif hasattr(event, 'message') and isinstance(event.message, Message):
            message = event.message

        if message is None:
            return await handler(event, data)

        now = time.monotonic()
        limit = RATE_LIMIT_MAX_ADMIN if is_admin else RATE_LIMIT_MAX

        # ── Cooldown ──────────────────────────────────────────────────────
        if not is_admin and user_id in _cooldowns:
            if now < _cooldowns[user_id]:
                remaining = int(_cooldowns[user_id] - now)
                if not _warned.get(user_id):
                    _warned[user_id] = True
                    await message.answer(
                        f"Please wait {remaining}s before sending more messages."
                    )
                logger.warning(f"User {user_id} in cooldown, {remaining}s left")
                return
            else:
                del _cooldowns[user_id]
                _warned.pop(user_id, None)
                _message_timestamps[user_id].clear()

        # ── Rate limit ────────────────────────────────────────────────────
        _message_timestamps[user_id].append(now)
        count = _count_recent(user_id, now)

        if count > limit:
            if not is_admin:
                _cooldowns[user_id] = now + COOLDOWN_DURATION
                _warned[user_id] = True
                logger.warning(f"Rate limit: user {user_id}, {count} msgs/{RATE_LIMIT_WINDOW}s")
                await message.answer(
                    f"You've sent {count} messages in under a minute. "
                    f"Take a breath — I'll be back in {COOLDOWN_DURATION} seconds. ☕"
                )
                return

        if count >= WARN_THRESHOLD and not _warned.get(user_id) and not is_admin:
            _warned[user_id] = True
            await message.answer(
                "You're sending messages very fast — slow down a little so I can keep up."
            )

        # ── Обрезаем огромный текст ───────────────────────────────────────
        if message.text and len(message.text) > MAX_MESSAGE_LENGTH:
            original_len = len(message.text)
            message.text = message.text[:MAX_MESSAGE_LENGTH] + " [...]"
            logger.info(f"Message from {user_id} truncated: {original_len} → {MAX_MESSAGE_LENGTH} chars")

        return await handler(event, data)
