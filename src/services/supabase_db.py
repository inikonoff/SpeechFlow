import logging
import re
from typing import Optional, Dict, Any, List
from datetime import datetime, timezone, timedelta
from supabase import create_client, Client

from src.config import settings, ADMIN_IDS

logger = logging.getLogger(__name__)


class SupabaseDB:
    def __init__(self):
        self.client: Client = create_client(settings.SUPABASE_URL, settings.SUPABASE_KEY)

    async def ping(self) -> bool:
        try:
            self.client.table("users").select("id").limit(1).execute()
            logger.info("Supabase DB pinged successfully.")
            return True
        except Exception as e:
            logger.error(f"Supabase DB ping failed: {e}")
            return False

    # ─── Пользователи ──────────────────────────────────────────────────────

    async def get_or_create_user(self, telegram_id: int, username: Optional[str] = None) -> Dict[str, Any]:
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
                "notifications_enabled": True,
                "vocab_practice_enabled": True  # Значение по умолчанию
            }
            response = self.client.table("users").insert(user_data).execute()
            return response.data[0]
        except Exception as e:
            logger.error(f"Error getting/creating user: {e}")
            return {}

    async def update_user(self, telegram_id: int, data: Dict[str, Any]) -> bool:
        try:
            self.client.table("users").update(data).eq("telegram_id", telegram_id).execute()
            return True
        except Exception as e:
            logger.error(f"Error updating user: {e}")
            return False

    async def toggle_vocab_practice_mode(self, telegram_id: int) -> Optional[bool]:
        """Переключает режим практики слов для пользователя"""
        try:
            user = await self.get_or_create_user(telegram_id)
            current_state = user.get("vocab_practice_enabled", True)
            new_state = not current_state
            
            self.client.table("users").update(
                {"vocab_practice_enabled": new_state}
            ).eq("telegram_id", telegram_id).execute()
            
            return new_state
        except Exception as e:
            logger.error(f"Error toggling vocab practice: {e}")
            return None

    # ─── Админ-панель ──────────────────────────────────────────────────────

    async def get_admin_stats(self) -> Dict[str, Any]:
        """Собирает статистику для админ-панели"""
        try:
            # Считаем пользователей
            users_res = self.client.table("users").select("id", count="exact").execute()
            # Считаем сообщения
            msg_res = self.client.table("messages").select("id", count="exact").execute()
            
            return {
                "total_users": users_res.count or 0,
                "total_messages": msg_res.count or 0
            }
        except Exception as e:
            logger.error(f"Error getting admin stats: {e}")
            return {"total_users": 0, "total_messages": 0}

    async def get_all_users(self, limit: int = 50) -> List[Dict[str, Any]]:
        """Возвращает список последних пользователей"""
        try:
            response = (self.client.table("users")
                        .select("*")
                        .order("created_at", desc=True)
                        .limit(limit)
                        .execute())
            return response.data or []
        except Exception as e:
            logger.error(f"Error getting all users: {e}")
            return []

    # ─── Работа с Summary и сообщениями ─────────────────────────────────────

    async def save_message(self, user_id: int, role: str, content: str, tokens: int = 0):
        try:
            data = {
                "user_id": user_id,
                "role": role,
                "content": content,
                "tokens": tokens
            }
            self.client.table("messages").insert(data).execute()
        except Exception as e:
            logger.error(f"Error saving message: {e}")

    async def get_user_summary(self, user_id: int) -> Optional[str]:
        try:
            response = (self.client.table("user_summaries")
                        .select("summary_text")
                        .eq("user_id", user_id)
                        .order("updated_at", desc=True)
                        .limit(1)
                        .execute())
            if response.data:
                return response.data[0]["summary_text"]
            return None
        except Exception as e:
            logger.error(f"Error getting summary: {e}")
            return None

    async def save_temp_summary(self, user_id: int, summary_text: str):
        try:
            self.client.table("summaries").insert({
                "user_id": user_id,
                "content": summary_text,
                "is_merged": False
            }).execute()
        except Exception as e:
            logger.error(f"Error saving temp summary: {e}")

    async def get_unmerged_summaries(self, user_id: int) -> List[Dict[str, Any]]:
        try:
            response = (self.client.table("summaries")
                        .select("*")
                        .eq("user_id", user_id)
                        .eq("is_merged", False)
                        .order("created_at", desc=False)
                        .execute())
            return response.data or []
        except Exception as e:
            logger.error(f"Error getting unmerged summaries: {e}")
            return []

    async def mark_summaries_as_merged(self, user_id: int, summary_ids: List[str]) -> bool:
        try:
            for sid in summary_ids:
                self.client.table("summaries").update(
                    {"is_merged": True}
                ).eq("id", sid).execute()
            return True
        except Exception as e:
            logger.error(f"Error marking summaries as merged: {e}")
            return False

    async def get_messages_for_summary(self, user_id: int, limit: int = 30) -> List[Dict[str, str]]:
        try:
            response = (self.client.table("messages")
                        .select("role, content")
                        .eq("user_id", user_id)
                        .order("created_at", desc=True)
                        .limit(limit)
                        .execute())
            if not response.data:
                return []
            return list(reversed(response.data))
        except Exception as e:
            logger.error(f"Error getting messages for summary: {e}")
            return []


db = SupabaseDB()
