import logging
from typing import Callable, Dict, Any, Awaitable
from aiogram import BaseMiddleware
from aiogram.types import TelegramObject

from src.services.supabase_db import db

logger = logging.getLogger(__name__)


class UserMiddleware(BaseMiddleware):
    """Middleware для загрузки данных пользователя в каждый апдейт"""
    
    async def __call__(
        self,
        handler: Callable[[TelegramObject, Dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: Dict[str, Any]
    ) -> Any:
        # Извлекаем ID пользователя из события
        user_id = None
        
        if hasattr(event, 'from_user') and event.from_user:
            user_id = event.from_user.id
        elif hasattr(event, 'message') and event.message and event.message.from_user:
            user_id = event.message.from_user.id
        elif hasattr(event, 'callback_query') and event.callback_query:
            user_id = event.callback_query.from_user.id
        
        if user_id:
            try:
                # Загружаем данные пользователя
                user = await db.get_or_create_user(user_id)
                data["user"] = user
                data["is_admin"] = await db.is_admin(user_id)
            except Exception as e:
                logger.error(f"Error loading user {user_id}: {e}")
                data["user"] = {}
                data["is_admin"] = False
        
        return await handler(event, data)
