import logging
from typing import Optional, Dict, Any, List
from datetime import datetime, timezone
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
                "created_at": datetime.now(timezone.utc).isoformat()
            }

            response = self.client.table("users").insert(user_data).execute()
            return response.data[0]

        except Exception as e:
            logger.error(f"Error in get_or_create_user: {e}")
            raise

    async def update_user_voice(self, telegram_id: int, voice: str) -> bool:
        try:
            response = (self.client
                        .table("users")
                        .update({"voice": voice})
                        .eq("telegram_id", telegram_id)
                        .execute())
            return len(response.data) > 0
        except Exception as e:
            logger.error(f"Error updating user voice: {e}")
            return False

    async def update_user_level(self, telegram_id: int, level: str) -> bool:
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

    async def update_user_persona(self, telegram_id: int, persona_key: str) -> bool:
        try:
            response = (self.client
                        .table("users")
                        .update({"persona": persona_key})
                        .eq("telegram_id", telegram_id)
                        .execute())
            return len(response.data) > 0
        except Exception as e:
            logger.error(f"Error updating user persona: {e}")
            return False

    async def increment_user_metrics(self, telegram_id: int, tokens_used: int = 0) -> None:
        try:
            user = await self.get_or_create_user(telegram_id)

            update_data = {
                "total_tokens_used": user.get("total_tokens_used", 0) + tokens_used,
                "free_messages_used": user.get("free_messages_used", 0) + 1
            }

            last_active = user.get("last_active")
            if last_active:
                last_date = datetime.fromisoformat(last_active.replace('Z', '+00:00')).date()
                today = datetime.now(timezone.utc).date()
                days_diff = (today - last_date).days

                if days_diff == 1:
                    update_data["streak_days"] = user.get("streak_days", 0) + 1
                elif days_diff > 1:
                    update_data["streak_days"] = 1

            update_data["last_active"] = datetime.now(timezone.utc).isoformat()
            self.client.table("users").update(update_data).eq("telegram_id", telegram_id).execute()
        except Exception as e:
            logger.error(f"Error incrementing metrics: {e}")

    # ─── Словарь ───────────────────────────────────────────────────────────

    async def add_to_vocabulary(self, telegram_id: int, word_data: Dict[str, Any]) -> bool:
        try:
            vocab_entry = {
                "user_id": telegram_id,
                "word_or_phrase": word_data.get("word_or_phrase", ""),
                "translation": word_data.get("translation", ""),
                "context_sentence": word_data.get("context_sentence", ""),
                "mastery_score": word_data.get("mastery_score", 0),
                "created_at": datetime.now(timezone.utc).isoformat()
            }
            response = self.client.table("vocabulary").insert(vocab_entry).execute()
            return len(response.data) > 0
        except Exception as e:
            logger.error(f"Error adding vocabulary: {e}")
            return False

    async def get_user_vocabulary(self, telegram_id: int, limit: int = 50) -> List[Dict[str, Any]]:
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
            logger.error(f"Error getting vocabulary: {e}")
            return []

    # ─── Ошибки ────────────────────────────────────────────────────────────

    async def log_error(self, telegram_id: int, error_data: Dict[str, Any]) -> bool:
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

    # ─── Статистика ────────────────────────────────────────────────────────

    async def get_user_stats(self, telegram_id: int) -> Dict[str, Any]:
        try:
            user = await self.get_or_create_user(telegram_id)

            error_response = (self.client
                              .table("error_logs")
                              .select("category")
                              .eq("user_id", telegram_id)
                              .execute())

            error_stats = {}
            if error_response.data:
                for item in error_response.data:
                    cat = item.get("category", "other")
                    error_stats[cat] = error_stats.get(cat, 0) + 1

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
            logger.error(f"Error getting stats: {e}")
            return {"user": {}, "vocabulary_count": 0, "error_stats": {}}

    async def is_admin(self, telegram_id: int) -> bool:
        return telegram_id in ADMIN_IDS

    # ─── История диалога ───────────────────────────────────────────────────

    async def save_message(self, user_id: int, role: str, content: str) -> bool:
        try:
            entry = {
                "user_id": user_id,
                "role": role,
                "content": content,
                "created_at": datetime.now(timezone.utc).isoformat()
            }
            response = self.client.table("messages").insert(entry).execute()
            return len(response.data) > 0
        except Exception as e:
            logger.error(f"Error saving message: {e}")
            return False

    async def get_history(self, user_id: int, limit: int = 5) -> List[Dict[str, str]]:
        try:
            response = (self.client
                        .table("messages")
                        .select("role, content")
                        .eq("user_id", user_id)
                        .order("created_at", desc=True)
                        .limit(limit)
                        .execute())

            if not response.data:
                return []

            messages = list(reversed(response.data))
            return [{"role": m["role"], "content": m["content"]} for m in messages]
        except Exception as e:
            logger.error(f"Error getting history: {e}")
            return []

    # ─── Саммаризация ──────────────────────────────────────────────────────

    async def save_summary(self, user_id: int, content: str, is_merged: bool = False) -> bool:
        """Сохраняет новый саммари диалога"""
        try:
            entry = {
                "user_id": user_id,
                "content": content,
                "is_merged": is_merged,
                "created_at": datetime.now(timezone.utc).isoformat()
            }
            response = self.client.table("summaries").insert(entry).execute()
            return len(response.data) > 0
        except Exception as e:
            logger.error(f"Error saving summary: {e}")
            return False

    async def get_summaries(self, user_id: int) -> List[Dict[str, Any]]:
        """Возвращает все саммари пользователя в хронологическом порядке"""
        try:
            response = (self.client
                        .table("summaries")
                        .select("*")
                        .eq("user_id", user_id)
                        .order("created_at", desc=False)
                        .execute())
            return response.data or []
        except Exception as e:
            logger.error(f"Error getting summaries: {e}")
            return []

    async def get_latest_summary(self, user_id: int) -> Optional[str]:
        """
        Возвращает актуальный контекст пользователя для инжекции в промпт.
        Если есть merged-саммари — берёт его + новые после него.
        Если нет — берёт все по порядку.
        """
        try:
            summaries = await self.get_summaries(user_id)
            if not summaries:
                return None

            # Ищем последний merged — он база
            merged = [s for s in summaries if s.get("is_merged")]
            unmerged = [s for s in summaries if not s.get("is_merged")]

            if merged:
                # Берём последний merged + все unmerged после него
                last_merged = merged[-1]
                last_merged_time = last_merged["created_at"]
                new_after_merge = [
                    s for s in unmerged
                    if s["created_at"] > last_merged_time
                ]
                parts = [last_merged["content"]] + [s["content"] for s in new_after_merge]
            else:
                parts = [s["content"] for s in unmerged]

            return "\n\n---\n\n".join(parts)
        except Exception as e:
            logger.error(f"Error getting latest summary: {e}")
            return None

    async def count_unmerged_summaries(self, user_id: int) -> int:
        """Считает количество несхлопнутых саммари"""
        try:
            response = (self.client
                        .table("summaries")
                        .select("id", count="exact")
                        .eq("user_id", user_id)
                        .eq("is_merged", False)
                        .execute())
            return response.count if hasattr(response, 'count') else 0
        except Exception as e:
            logger.error(f"Error counting summaries: {e}")
            return 0

    async def get_unmerged_summaries(self, user_id: int) -> List[Dict[str, Any]]:
        """Возвращает все несхлопнутые саммари"""
        try:
            response = (self.client
                        .table("summaries")
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
        """Помечает старые саммари как вошедшие в схлопывание"""
        try:
            for sid in summary_ids:
                self.client.table("summaries").update({"is_merged": True}).eq("id", sid).execute()
            return True
        except Exception as e:
            logger.error(f"Error marking summaries as merged: {e}")
            return False

    async def get_messages_for_summary(self, user_id: int, limit: int = 30) -> List[Dict[str, str]]:
        """
        Возвращает последние N сообщений для саммаризации.
        Используется при детектировании прощания.
        """
        try:
            response = (self.client
                        .table("messages")
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
