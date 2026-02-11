import logging
from typing import Optional, Dict, Any, List
from datetime import datetime, timezone
from supabase import create_client, Client

from src.config import settings

logger = logging.getLogger(__name__)


class SupabaseDB:
    def __init__(self):
        self.client: Client = create_client(settings.SUPABASE_URL, settings.SUPABASE_KEY)
    
    async def get_or_create_user(self, telegram_id: int, username: Optional[str] = None) -> Dict[str, Any]:
        """Получаем или создаем пользователя"""
        try:
            response = (self.client
                       .table("users")
                       .select("*")
                       .eq("telegram_id", telegram_id)
                       .execute())
            
            if response.data:
                return response.data[0]
            
            user_data = {
                "telegram_id": telegram_id,
                "username": username,
                "level": settings.DEFAULT_USER_LEVEL,
                "streak_days": 0,
                "total_tokens_used": 0,
                "free_messages_used": 0,
                "created_at": datetime.now(timezone.utc).isoformat()
            }
            
            response = self.client.table("users").insert(user_data).execute()
            return response.data[0]
            
        except Exception as e:
            logger.error(f"Error in get_or_create_user: {e}")
            raise
    
    async def update_user_level(self, telegram_id: int, level: str) -> bool:
        """Обновляем уровень пользователя"""
        try:
            response = (self.client
                       .table("users")
                       .update({"level": level})
                       .eq("telegram_id", telegram_id)
                       .execute())
            return len(response.data) > 0
        except Exception as e:
            logger.error(f"Error updating user level: {e}")
            return False
    
    async def increment_user_metrics(self, telegram_id: int, tokens_used: int = 0) -> None:
        """Обновляем метрики пользователя"""
        try:
            user = await self.get_or_create_user(telegram_id)
            
            update_data = {
                "total_tokens_used": user.get("total_tokens_used", 0) + tokens_used,
                "free_messages_used": user.get("free_messages_used", 0) + 1
            }
            
            # Простая логика streak: увеличиваем каждый день при использовании
            last_active = user.get("last_active")
            if last_active:
                last_date = datetime.fromisoformat(last_active.replace('Z', '+00:00')).date()
                today = datetime.now(timezone.utc).date()
                
                if today > last_date:
                    if today.day - last_date.day == 1:
                        update_data["streak_days"] = user.get("streak_days", 0) + 1
                    else:
                        update_data["streak_days"] = 1
            
            update_data["last_active"] = datetime.now(timezone.utc).isoformat()
            
            response = (self.client
                       .table("users")
                       .update(update_data)
                       .eq("telegram_id", telegram_id)
                       .execute())
            
        except Exception as e:
            logger.error(f"Error incrementing user metrics: {e}")
    
    async def add_to_vocabulary(self, telegram_id: int, word_data: Dict[str, Any]) -> bool:
        """Добавляем слово/фразу в словарь пользователя"""
        try:
            vocab_entry = {
                "user_id": telegram_id,
                "word_or_phrase": word_data.get("word_or_phrase"),
                "translation": word_data.get("translation"),
                "context_sentence": word_data.get("context_sentence"),
                "mastery_score": word_data.get("mastery_score", 0),
                "created_at": datetime.now(timezone.utc).isoformat()
            }
            
            response = self.client.table("vocabulary").insert(vocab_entry).execute()
            return len(response.data) > 0
            
        except Exception as e:
            logger.error(f"Error adding to vocabulary: {e}")
            return False
    
    async def log_error(self, telegram_id: int, error_data: Dict[str, Any]) -> bool:
        """Логируем ошибку пользователя"""
        try:
            error_entry = {
                "user_id": telegram_id,
                "category": error_data.get("category"),
                "mistake_text": error_data.get("mistake_text"),
                "created_at": datetime.now(timezone.utc).isoformat()
            }
            
            response = self.client.table("error_logs").insert(error_entry).execute()
            return len(response.data) > 0
            
        except Exception as e:
            logger.error(f"Error logging error: {e}")
            return False
    
    async def get_user_vocabulary(self, telegram_id: int, limit: int = 50) -> List[Dict[str, Any]]:
        """Получаем словарь пользователя"""
        try:
            response = (self.client
                       .table("vocabulary")
                       .select("*")
                       .eq("user_id", telegram_id)
                       .order("created_at", desc=True)
                       .limit(limit)
                       .execute())
            return response.data
        except Exception as e:
            logger.error(f"Error getting user vocabulary: {e}")
            return []
    
    async def get_user_stats(self, telegram_id: int) -> Dict[str, Any]:
        """Получаем статистику пользователя"""
        try:
            user = await self.get_or_create_user(telegram_id)
            
            # Получаем количество ошибок по категориям
            error_response = (self.client
                            .table("error_logs")
                            .select("category", count="exact")
                            .eq("user_id", telegram_id)
                            .execute())
            
            error_stats = {}
            if error_response.data:
                for item in error_response.data:
                    error_stats[item["category"]] = item.get("count", 0)
            
            # Получаем количество слов в словаре
            vocab_response = (self.client
                            .table("vocabulary")
                            .select("id", count="exact")
                            .eq("user_id", telegram_id)
                            .execute())
            
            return {
                "user": user,
                "vocabulary_count": vocab_response.count if hasattr(vocab_response, 'count') else 0,
                "error_stats": error_stats
            }
            
        except Exception as e:
            logger.error(f"Error getting user stats: {e}")
            return {"user": {}, "vocabulary_count": 0, "error_stats": {}}
    
    async def is_admin(self, telegram_id: int) -> bool:
        """Проверяем, является ли пользователь админом"""
        return telegram_id in settings.ADMIN_IDS


# Глобальный экземпляр
db = SupabaseDB()
