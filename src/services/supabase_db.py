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
                "vocabulary_remind_counter": 0,
                "created_at": datetime.now(timezone.utc).isoformat()
            }

            response = self.client.table("users").insert(user_data).execute()
            return response.data[0]

        except Exception as e:
            logger.error(f"Error in get_or_create_user: {e}")
            raise

    async def update_user_voice(self, telegram_id: int, voice: str) -> bool:
        try:
            response = (self.client.table("users")
                        .update({"voice": voice})
                        .eq("telegram_id", telegram_id)
                        .execute())
            return len(response.data) > 0
        except Exception as e:
            logger.error(f"Error updating user voice: {e}")
            return False

    async def update_user_level(self, telegram_id: int, level: str) -> bool:
        try:
            response = (self.client.table("users")
                        .update({"level": level})
                        .eq("telegram_id", telegram_id)
                        .execute())
            return len(response.data) > 0
        except Exception as e:
            logger.error(f"Error updating user level: {e}")
            return False

    async def update_user_persona(self, telegram_id: int, persona_key: str) -> bool:
        try:
            response = (self.client.table("users")
                        .update({"persona": persona_key})
                        .eq("telegram_id", telegram_id)
                        .execute())
            return len(response.data) > 0
        except Exception as e:
            logger.error(f"Error updating user persona: {e}")
            return False

    async def update_notifications(self, telegram_id: int, enabled: bool) -> bool:
        try:
            response = (self.client.table("users")
                        .update({"notifications_enabled": enabled})
                        .eq("telegram_id", telegram_id)
                        .execute())
            return len(response.data) > 0
        except Exception as e:
            logger.error(f"Error updating notifications: {e}")
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

    async def increment_vocab_remind_counter(self, telegram_id: int) -> int:
        """Увеличивает счётчик сообщений для напоминания о словах. Возвращает новое значение."""
        try:
            user = await self.get_or_create_user(telegram_id)
            new_val = user.get("vocabulary_remind_counter", 0) + 1
            self.client.table("users").update(
                {"vocabulary_remind_counter": new_val}
            ).eq("telegram_id", telegram_id).execute()
            return new_val
        except Exception as e:
            logger.error(f"Error incrementing vocab remind counter: {e}")
            return 0

    async def reset_vocab_remind_counter(self, telegram_id: int) -> None:
        try:
            self.client.table("users").update(
                {"vocabulary_remind_counter": 0}
            ).eq("telegram_id", telegram_id).execute()
        except Exception as e:
            logger.error(f"Error resetting vocab remind counter: {e}")

    async def increment_session_count(self, telegram_id: int) -> int:
        """Увеличивает счётчик завершённых сессий. Вызывается при прощании. Возвращает новое значение."""
        try:
            user = await self.get_or_create_user(telegram_id)
            new_val = user.get("session_count", 0) + 1
            self.client.table("users").update(
                {"session_count": new_val}
            ).eq("telegram_id", telegram_id).execute()
            return new_val
        except Exception as e:
            logger.error(f"Error incrementing session count: {e}")
            return 0

    async def update_mode(self, telegram_id: int, mode: str) -> bool:
        """Сохраняет активный режим: tutor / penfriend / flow"""
        try:
            self.client.table("users").update(
                {"mode": mode}
            ).eq("telegram_id", telegram_id).execute()
            return True
        except Exception as e:
            logger.error(f"Error updating mode: {e}")
            return False

    async def update_correction_rate(self, telegram_id: int, rate: int) -> bool:
        """Сохраняет степень придирчивости коррекции для PenFriend Mode"""
        try:
            self.client.table("users").update(
                {"correction_rate": rate}
            ).eq("telegram_id", telegram_id).execute()
            return True
        except Exception as e:
            logger.error(f"Error updating correction rate: {e}")
            return False

    # ─── Словарь ───────────────────────────────────────────────────────────

    async def add_to_vocabulary(self, telegram_id: int, word_data: Dict[str, Any]) -> bool:
        try:
            vocab_entry = {
                "user_id": telegram_id,
                "word_or_phrase": word_data.get("word_or_phrase", ""),
                "translation": word_data.get("translation", ""),
                "context_sentence": word_data.get("context_sentence", ""),
                "mastery_score": 0,
                "created_at": datetime.now(timezone.utc).isoformat()
            }
            response = self.client.table("vocabulary").insert(vocab_entry).execute()
            return len(response.data) > 0
        except Exception as e:
            logger.error(f"Error adding vocabulary: {e}")
            return False

    async def get_user_vocabulary(self, telegram_id: int, limit: int = 50) -> List[Dict[str, Any]]:
        try:
            response = (self.client.table("vocabulary")
                        .select("*")
                        .eq("user_id", telegram_id)
                        .order("created_at", desc=True)
                        .limit(limit)
                        .execute())
            return response.data or []
        except Exception as e:
            logger.error(f"Error getting vocabulary: {e}")
            return []

    async def get_word_for_reminder(self, telegram_id: int) -> Optional[Dict[str, Any]]:
        """
        Возвращает слово для напоминания:
        приоритет — mastery_score < 5, давно не напоминали (или ни разу).
        """
        try:
            response = (self.client.table("vocabulary")
                        .select("*")
                        .eq("user_id", telegram_id)
                        .lt("mastery_score", 5)
                        .order("last_reminded_at", desc=False, nullsfirst=True)
                        .limit(1)
                        .execute())
            return response.data[0] if response.data else None
        except Exception as e:
            logger.error(f"Error getting word for reminder: {e}")
            return None

    async def mark_word_reminded(self, word_id: str) -> bool:
        """Обновляет last_reminded_at для слова"""
        try:
            response = (self.client.table("vocabulary")
                        .update({"last_reminded_at": datetime.now(timezone.utc).isoformat()})
                        .eq("id", word_id)
                        .execute())
            return len(response.data) > 0
        except Exception as e:
            logger.error(f"Error marking word reminded: {e}")
            return False

    async def increase_mastery(self, word_id: str) -> int:
        """Увеличивает mastery_score на 1. Возвращает новое значение."""
        try:
            current = (self.client.table("vocabulary")
                       .select("mastery_score")
                       .eq("id", word_id)
                       .execute())
            if not current.data:
                return 0
            new_score = min(current.data[0]["mastery_score"] + 1, 10)
            self.client.table("vocabulary").update(
                {"mastery_score": new_score}
            ).eq("id", word_id).execute()
            return new_score
        except Exception as e:
            logger.error(f"Error increasing mastery: {e}")
            return 0

    async def find_word_in_text(self, telegram_id: int, text: str) -> Optional[Dict[str, Any]]:
        """
        Проверяет использовал ли пользователь напомянутое слово в тексте.
        Возвращает запись словаря если нашёл.
        """
        try:
            # Берём слова которые напоминали за последние 10 минут
            since = (datetime.now(timezone.utc) - timedelta(minutes=10)).isoformat()
            response = (self.client.table("vocabulary")
                        .select("*")
                        .eq("user_id", telegram_id)
                        .gte("last_reminded_at", since)
                        .lt("mastery_score", 5)
                        .execute())

            if not response.data:
                return None

            text_lower = text.lower()
            for word in response.data:
                phrase = word.get("word_or_phrase", "").lower()
                if phrase and phrase in text_lower:
                    return word
            return None
        except Exception as e:
            logger.error(f"Error finding word in text: {e}")
            return None

    # ─── Ошибки ────────────────────────────────────────────────────────────

    async def get_top_error_categories(self, telegram_id: int, limit: int = 2) -> List[str]:
        """Возвращает топ категорий ошибок пользователя по частоте"""
        try:
            response = (self.client.table("error_logs")
                        .select("category")
                        .eq("user_id", telegram_id)
                        .execute())
            if not response.data:
                return []
            counts: Dict[str, int] = {}
            for row in response.data:
                cat = row.get("category", "").strip().lower()
                if cat and cat != "none":
                    counts[cat] = counts.get(cat, 0) + 1
            sorted_cats = sorted(counts.items(), key=lambda x: x[1], reverse=True)
            return [cat for cat, _ in sorted_cats[:limit]]
        except Exception as e:
            logger.error(f"Error getting top error categories: {e}")
            return []

    async def log_error(self, telegram_id: int, error_data: Dict[str, Any]) -> bool:
        try:
            raw_category = error_data.get("category", "other") or "other"
            # Нормализуем составные категории типа "grammar|structure" → "grammar"
            category = re.split(r'[|/,]', raw_category.strip().lower())[0].strip()
            if not category or category == "none":
                return True
            error_entry = {
                "user_id": telegram_id,
                "category": category,
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

            # Ошибки за всё время
            error_response = (self.client.table("error_logs")
                              .select("category, created_at")
                              .eq("user_id", telegram_id)
                              .execute())

            error_stats = {}
            error_stats_week = {}
            error_stats_prev_week = {}

            now = datetime.now(timezone.utc)
            week_ago = (now - timedelta(days=7)).isoformat()
            two_weeks_ago = (now - timedelta(days=14)).isoformat()

            if error_response.data:
                for item in error_response.data:
                    cat = item.get("category", "other")
                    created = item.get("created_at", "")
                    error_stats[cat] = error_stats.get(cat, 0) + 1

                    if created >= week_ago:
                        error_stats_week[cat] = error_stats_week.get(cat, 0) + 1
                    elif created >= two_weeks_ago:
                        error_stats_prev_week[cat] = error_stats_prev_week.get(cat, 0) + 1

            # Словарь
            vocab_response = (self.client.table("vocabulary")
                              .select("id, mastery_score", count="exact")
                              .eq("user_id", telegram_id)
                              .execute())

            mastered_count = sum(
                1 for v in (vocab_response.data or [])
                if v.get("mastery_score", 0) >= 5
            )

            # Сообщения за эту и прошлую неделю (для динамики активности)
            msgs_this_week = (self.client.table("messages")
                              .select("id", count="exact")
                              .eq("user_id", telegram_id)
                              .eq("role", "user")
                              .gte("created_at", week_ago)
                              .execute())

            msgs_prev_week = (self.client.table("messages")
                              .select("id", count="exact")
                              .eq("user_id", telegram_id)
                              .eq("role", "user")
                              .gte("created_at", two_weeks_ago)
                              .lt("created_at", week_ago)
                              .execute())

            return {
                "user": user,
                "vocabulary_count": vocab_response.count or 0,
                "mastered_count": mastered_count,
                "error_stats": error_stats,
                "error_stats_week": error_stats_week,
                "error_stats_prev_week": error_stats_prev_week,
                "msgs_this_week": msgs_this_week.count or 0,
                "msgs_prev_week": msgs_prev_week.count or 0,
            }
        except Exception as e:
            logger.error(f"Error getting stats: {e}")
            return {"user": {}, "vocabulary_count": 0, "mastered_count": 0,
                    "error_stats": {}, "error_stats_week": {}, "error_stats_prev_week": {},
                    "msgs_this_week": 0, "msgs_prev_week": 0}

    async def is_admin(self, telegram_id: int) -> bool:
        return telegram_id in ADMIN_IDS

    # ─── Уведомления ───────────────────────────────────────────────────────

    async def get_users_for_notification(self) -> List[Dict[str, Any]]:
        """
        Возвращает пользователей которым нужно отправить уведомление:
        - notifications_enabled = true
        - last_active > 48 часов назад
        - last_notified_at > 48 часов назад (или null)
        """
        try:
            threshold = (datetime.now(timezone.utc) - timedelta(hours=48)).isoformat()
            response = (self.client.table("users")
                        .select("*")
                        .eq("notifications_enabled", True)
                        .lt("last_active", threshold)
                        .execute())

            if not response.data:
                return []

            result = []
            for user in response.data:
                last_notified = user.get("last_notified_at")
                if last_notified is None or last_notified < threshold:
                    result.append(user)
            return result
        except Exception as e:
            logger.error(f"Error getting users for notification: {e}")
            return []

    async def mark_user_notified(self, telegram_id: int) -> bool:
        try:
            response = (self.client.table("users")
                        .update({"last_notified_at": datetime.now(timezone.utc).isoformat()})
                        .eq("telegram_id", telegram_id)
                        .execute())
            return len(response.data) > 0
        except Exception as e:
            logger.error(f"Error marking user notified: {e}")
            return False

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
            response = (self.client.table("messages")
                        .select("role, content")
                        .eq("user_id", user_id)
                        .order("created_at", desc=True)
                        .limit(limit)
                        .execute())

            if not response.data:
                return []

            return [{"role": m["role"], "content": m["content"]}
                    for m in reversed(response.data)]
        except Exception as e:
            logger.error(f"Error getting history: {e}")
            return []

    # ─── Саммаризация ──────────────────────────────────────────────────────

    async def save_summary(self, user_id: int, content: str, is_merged: bool = False) -> bool:
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
        try:
            response = (self.client.table("summaries")
                        .select("*")
                        .eq("user_id", user_id)
                        .order("created_at", desc=False)
                        .execute())
            return response.data or []
        except Exception as e:
            logger.error(f"Error getting summaries: {e}")
            return []

    async def get_latest_summary(self, user_id: int) -> Optional[str]:
        try:
            summaries = await self.get_summaries(user_id)
            if not summaries:
                return None

            merged = [s for s in summaries if s.get("is_merged")]
            unmerged = [s for s in summaries if not s.get("is_merged")]

            if merged:
                last_merged = merged[-1]
                new_after = [s for s in unmerged
                             if s["created_at"] > last_merged["created_at"]]
                parts = [last_merged["content"]] + [s["content"] for s in new_after]
            else:
                parts = [s["content"] for s in unmerged]

            return "\n\n---\n\n".join(parts)
        except Exception as e:
            logger.error(f"Error getting latest summary: {e}")
            return None

    async def count_unmerged_summaries(self, user_id: int) -> int:
        try:
            response = (self.client.table("summaries")
                        .select("id", count="exact")
                        .eq("user_id", user_id)
                        .eq("is_merged", False)
                        .execute())
            return response.count or 0
        except Exception as e:
            logger.error(f"Error counting summaries: {e}")
            return 0

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
