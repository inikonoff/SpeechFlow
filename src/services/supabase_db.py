import logging
import re
from typing import Optional, Dict, Any, List
from datetime import datetime, timezone, timedelta
from supabase import create_client, Client

from src.config import settings, ADMIN_IDS

logger = logging.getLogger(__name__)

ERROR_MASTERY_THRESHOLD = 3  # сколько раз правильно употребил → Mastered


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
                self.client.table("users").update({"last_active": datetime.utcnow().isoformat()}).eq("telegram_id", telegram_id).execute()
                return response.data[0]

            user_data = {
                "telegram_id": telegram_id,
                "username": username,
                "level": settings.DEFAULT_USER_LEVEL,
                "mode": "flow",
                "persona": "greg",
                "voice": "austin",
                "streak_days": 0,
                "total_tokens_used": 0,
                "free_messages_used": 0,
                "notifications_enabled": True,
                "mistakes_practice_enabled": False,
                "correction_rate": settings.CORRECTION_RATE_DEFAULT,
                "session_count": 0,
                "last_active": datetime.utcnow().isoformat()
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

    async def update_user_level(self, telegram_id: int, level: str) -> bool:
        return await self.update_user(telegram_id, {"level": level})

    async def update_mode(self, telegram_id: int, mode: str) -> bool:
        return await self.update_user(telegram_id, {"mode": mode})

    async def update_user_persona(self, telegram_id: int, persona: str) -> bool:
        return await self.update_user(telegram_id, {"persona": persona})

    async def update_user_voice(self, telegram_id: int, voice: str) -> bool:
        return await self.update_user(telegram_id, {"voice": voice})

    async def update_notifications(self, telegram_id: int, enabled: bool) -> bool:
        return await self.update_user(telegram_id, {"notifications_enabled": enabled})

    async def toggle_mistakes_practice(self, telegram_id: int) -> bool:
        user = await self.get_or_create_user(telegram_id)
        new_val = not user.get("mistakes_practice_enabled", False)
        await self.update_user(telegram_id, {"mistakes_practice_enabled": new_val})
        return new_val

    async def update_correction_rate(self, telegram_id: int, rate: int) -> bool:
        return await self.update_user(telegram_id, {"correction_rate": rate})

    async def increment_user_metrics(self, telegram_id: int, tokens_used: int = 0) -> bool:
        try:
            user = await self.get_or_create_user(telegram_id)
            new_tokens = user.get("total_tokens_used", 0) + tokens_used
            new_free = user.get("free_messages_used", 0) + 1
            await self.update_user(telegram_id, {
                "total_tokens_used": new_tokens,
                "free_messages_used": new_free
            })
            return True
        except Exception as e:
            logger.error(f"Error incrementing metrics: {e}")
            return False

    async def increment_session_count(self, telegram_id: int) -> bool:
        try:
            user = await self.get_or_create_user(telegram_id)
            new_count = user.get("session_count", 0) + 1
            await self.update_user(telegram_id, {"session_count": new_count})
            return True
        except Exception as e:
            logger.error(f"Error incrementing session count: {e}")
            return False

    async def get_all_user_ids(self) -> List[int]:
        try:
            response = self.client.table("users").select("telegram_id").execute()
            return [u["telegram_id"] for u in response.data] if response.data else []
        except Exception as e:
            logger.error(f"Error getting all user ids: {e}")
            return []

    async def get_users_for_notification(self) -> List[Dict[str, Any]]:
        try:
            cutoff = (datetime.utcnow() - timedelta(hours=23.5)).isoformat()
            response = (self.client.table("users")
                        .select("*")
                        .eq("notifications_enabled", True)
                        .lt("last_active", cutoff)
                        .execute())
            return response.data or []
        except Exception as e:
            logger.error(f"Error getting users for notification: {e}")
            return []

    async def mark_user_notified(self, telegram_id: int) -> bool:
        return await self.update_user(telegram_id, {"last_active": datetime.utcnow().isoformat()})

    # ─── Админ-панель ──────────────────────────────────────────────────────

    async def get_admin_stats(self) -> Dict[str, Any]:
        try:
            users_res = self.client.table("users").select("*").execute()
            users = users_res.data or []

            now = datetime.now(timezone.utc)
            start_of_today = now.replace(hour=0, minute=0, second=0, microsecond=0)
            start_of_week = start_of_today - timedelta(days=now.weekday())

            new_today = 0
            new_week = 0
            active_week = 0
            modes = {}
            personas = {}

            for u in users:
                created_at_str = u.get("created_at")
                last_active_str = u.get("last_active") or created_at_str

                try:
                    if created_at_str:
                        created_dt = datetime.fromisoformat(created_at_str.replace("Z", "+00:00"))
                        if created_dt >= start_of_today:
                            new_today += 1
                        if created_dt >= start_of_week:
                            new_week += 1
                    if last_active_str:
                        active_dt = datetime.fromisoformat(last_active_str.replace("Z", "+00:00"))
                        if active_dt >= start_of_week:
                            active_week += 1
                except Exception:
                    pass

                mode = u.get("mode") or "flow"
                modes[mode] = modes.get(mode, 0) + 1
                persona = u.get("persona") or "greg"
                personas[persona] = personas.get(persona, 0) + 1

            return {
                "total": len(users),
                "new_today": new_today,
                "new_week": new_week,
                "active_week": active_week,
                "mode_ranking": sorted(modes.items(), key=lambda x: x[1], reverse=True),
                "top_personas": sorted(personas.items(), key=lambda x: x[1], reverse=True)[:5]
            }
        except Exception as e:
            logger.error(f"Error getting admin stats: {e}")
            return {"total": 0, "new_today": 0, "new_week": 0, "active_week": 0, "mode_ranking": [], "top_personas": []}

    async def get_all_users(self, limit: int = 50) -> List[Dict[str, Any]]:
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

    async def get_user_card(self, telegram_id: int) -> Optional[Dict[str, Any]]:
        try:
            user = await self.get_or_create_user(telegram_id)
            if not user:
                return None

            msgs_res = self.client.table("messages").select("id", count="exact").eq("user_id", telegram_id).execute()
            errors_res = self.client.table("error_logs").select("id", count="exact").eq("user_id", telegram_id).lt("mastery_score", ERROR_MASTERY_THRESHOLD).execute()
            mastered_res = self.client.table("error_logs").select("id", count="exact").eq("user_id", telegram_id).gte("mastery_score", ERROR_MASTERY_THRESHOLD).execute()

            now = datetime.now(timezone.utc)
            start_of_week = (now - timedelta(days=now.weekday())).isoformat()
            week_msgs_res = self.client.table("messages").select("id", count="exact").eq("user_id", telegram_id).gte("created_at", start_of_week).execute()

            return {
                "user": user,
                "msgs_total": msgs_res.count or 0,
                "msgs_week": week_msgs_res.count or 0,
                "errors_active": errors_res.count or 0,
                "mastered_count": mastered_res.count or 0
            }
        except Exception as e:
            logger.error(f"Error getting user card: {e}")
            return None

    # ─── История сообщений ──────────────────────────────────────────────────

    async def save_message(self, user_id: int, role: str, content: str, tokens: int = 0):
        try:
            self.client.table("messages").insert({
                "user_id": user_id,
                "role": role,
                "content": content,
            }).execute()
        except Exception as e:
            logger.error(f"Error saving message: {e}")

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
            return list(reversed(response.data))
        except Exception as e:
            logger.error(f"Error getting history: {e}")
            return []

    async def get_messages_for_summary(self, user_id: int, limit: int = 30) -> List[Dict[str, str]]:
        return await self.get_history(user_id, limit)

    # ─── Саммари ────────────────────────────────────────────────────────────

    async def get_latest_summary(self, user_id: int) -> Optional[str]:
        try:
            response = (self.client.table("summaries")
                        .select("content")
                        .eq("user_id", user_id)
                        .eq("is_merged", True)
                        .order("created_at", desc=True)
                        .limit(1)
                        .execute())
            if response.data:
                return response.data[0]["content"]
            return None
        except Exception as e:
            logger.error(f"Error getting summary: {e}")
            return None

    async def get_topics_to_discuss(self, user_id: int) -> Optional[str]:
        try:
            response = (self.client.table("summaries")
                        .select("topics_to_discuss")
                        .eq("user_id", user_id)
                        .eq("is_merged", True)
                        .order("created_at", desc=True)
                        .limit(1)
                        .execute())
            if response.data:
                return response.data[0].get("topics_to_discuss") or None
            return None
        except Exception as e:
            logger.error(f"Error getting topics: {e}")
            return None

    async def save_summary(self, user_id: int, content: str, is_merged: bool = False, topics: Optional[str] = None):
        try:
            data = {"user_id": user_id, "content": content, "is_merged": is_merged}
            if topics:
                data["topics_to_discuss"] = topics
            self.client.table("summaries").insert(data).execute()
        except Exception as e:
            logger.error(f"Error saving summary: {e}")

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

    async def count_unmerged_summaries(self, user_id: int) -> int:
        try:
            response = self.client.table("summaries").select("id", count="exact").eq("user_id", user_id).eq("is_merged", False).execute()
            return response.count or 0
        except Exception as e:
            logger.error(f"Error counting unmerged summaries: {e}")
            return 0

    async def mark_summaries_as_merged(self, user_id: int, summary_ids: List[str]) -> bool:
        if not summary_ids:
            return True
        try:
            for sid in summary_ids:
                self.client.table("summaries").update({"is_merged": True}).eq("id", sid).execute()
            return True
        except Exception as e:
            logger.error(f"Error marking summaries as merged: {e}")
            return False

    # ─── Статистика ─────────────────────────────────────────────────────────

    async def get_user_stats(self, telegram_id: int) -> Dict[str, Any]:
        try:
            user = await self.get_or_create_user(telegram_id)

            now = datetime.now(timezone.utc)
            start_of_week = (now - timedelta(days=now.weekday())).isoformat()
            prev_week_start = (now - timedelta(days=now.weekday() + 7)).isoformat()

            msgs_this = self.client.table("messages").select("id", count="exact").eq("user_id", telegram_id).gte("created_at", start_of_week).execute()
            msgs_prev = self.client.table("messages").select("id", count="exact").eq("user_id", telegram_id).gte("created_at", prev_week_start).lt("created_at", start_of_week).execute()

            errors_this = self.client.table("error_logs").select("category").eq("user_id", telegram_id).gte("created_at", start_of_week).execute()
            errors_prev = self.client.table("error_logs").select("category").eq("user_id", telegram_id).gte("created_at", prev_week_start).lt("created_at", start_of_week).execute()

            active_errors = self.client.table("error_logs").select("id", count="exact").eq("user_id", telegram_id).lt("mastery_score", ERROR_MASTERY_THRESHOLD).execute()
            mastered_errors = self.client.table("error_logs").select("id", count="exact").eq("user_id", telegram_id).gte("mastery_score", ERROR_MASTERY_THRESHOLD).execute()

            error_stats_week = {}
            for e in (errors_this.data or []):
                cat = e.get("category", "other")
                if cat.lower() != "none":
                    error_stats_week[cat] = error_stats_week.get(cat, 0) + 1

            error_stats_prev = {}
            for e in (errors_prev.data or []):
                cat = e.get("category", "other")
                if cat.lower() != "none":
                    error_stats_prev[cat] = error_stats_prev.get(cat, 0) + 1

            return {
                "user": user,
                "msgs_this_week": msgs_this.count or 0,
                "msgs_prev_week": msgs_prev.count or 0,
                "error_stats_week": error_stats_week,
                "error_stats_prev_week": error_stats_prev,
                "active_errors_count": active_errors.count or 0,
                "mastered_errors_count": mastered_errors.count or 0,
            }
        except Exception as e:
            logger.error(f"Error getting user stats: {e}")
            return {"user": {"level": "unknown", "persona": "greg", "streak_days": 0}}

    # ─── Error Logs ─────────────────────────────────────────────────────────

    async def log_error(self, user_id: int, error_data: Dict[str, str]) -> bool:
        """Логирует ошибку. Если такая категория уже есть — обновляет mistake_text и corrected_text."""
        try:
            category = error_data.get("category", "other")
            mistake_text = error_data.get("mistake_text", "")
            corrected_text = error_data.get("corrected_text", "")

            # Проверяем есть ли уже такая категория у юзера (не mastered)
            existing = (self.client.table("error_logs")
                        .select("id, times_corrected")
                        .eq("user_id", user_id)
                        .eq("category", category)
                        .lt("mastery_score", ERROR_MASTERY_THRESHOLD)
                        .limit(1)
                        .execute())

            if existing.data:
                # Обновляем пример ошибки свежим
                row_id = existing.data[0]["id"]
                self.client.table("error_logs").update({
                    "mistake_text": mistake_text,
                    "corrected_text": corrected_text,
                }).eq("id", row_id).execute()
            else:
                self.client.table("error_logs").insert({
                    "user_id": user_id,
                    "category": category,
                    "mistake_text": mistake_text,
                    "corrected_text": corrected_text,
                    "mastery_score": 0,
                    "times_corrected": 0,
                }).execute()
            return True
        except Exception as e:
            logger.error(f"Error logging error: {e}")
            return False

    async def get_error_for_practice(self, user_id: int) -> Optional[Dict[str, Any]]:
        """Возвращает одну активную ошибку для практики — самую частую (по порядку created_at)."""
        try:
            response = (self.client.table("error_logs")
                        .select("*")
                        .eq("user_id", user_id)
                        .lt("mastery_score", ERROR_MASTERY_THRESHOLD)
                        .order("created_at", desc=False)
                        .limit(1)
                        .execute())
            if response.data:
                return response.data[0]
            return None
        except Exception as e:
            logger.error(f"Error getting error for practice: {e}")
            return None

    async def increase_error_mastery(self, error_id: int) -> int:
        """Инкрементирует mastery_score ошибки. Возвращает новый score."""
        try:
            row = self.client.table("error_logs").select("mastery_score, times_corrected").eq("id", error_id).execute()
            if not row.data:
                return 0
            current_score = row.data[0].get("mastery_score", 0)
            times_corrected = row.data[0].get("times_corrected", 0)
            new_score = current_score + 1
            self.client.table("error_logs").update({
                "mastery_score": new_score,
                "times_corrected": times_corrected + 1,
            }).eq("id", error_id).execute()
            return new_score
        except Exception as e:
            logger.error(f"Error increasing error mastery: {e}")
            return 0

    async def get_user_errors(self, user_id: int, tab: str = "mistakes", limit: int = 20) -> List[Dict[str, Any]]:
        """Возвращает ошибки для Practice Log. tab: mistakes | mastered"""
        try:
            query = self.client.table("error_logs").select("*").eq("user_id", user_id)
            if tab == "mistakes":
                query = query.lt("mastery_score", ERROR_MASTERY_THRESHOLD)
            elif tab == "mastered":
                query = query.gte("mastery_score", ERROR_MASTERY_THRESHOLD)
            response = query.order("created_at", desc=True).limit(limit).execute()
            return response.data or []
        except Exception as e:
            logger.error(f"Error getting user errors: {e}")
            return []

    async def get_top_error_categories(self, user_id: int, limit: int = 2) -> List[str]:
        """Топ категорий ошибок для подсказки модели в Flow/PenFriend."""
        try:
            response = (self.client.table("error_logs")
                        .select("category")
                        .eq("user_id", user_id)
                        .lt("mastery_score", ERROR_MASTERY_THRESHOLD)
                        .execute())
            if not response.data:
                return []
            counts = {}
            for row in response.data:
                c = row.get("category")
                if c and c.lower() != "none":
                    counts[c] = counts.get(c, 0) + 1
            sorted_cats = sorted(counts.items(), key=lambda x: x[1], reverse=True)
            return [cat[0] for cat in sorted_cats[:limit]]
        except Exception as e:
            logger.error(f"Error getting top errors: {e}")
            return []

    async def get_weekly_errors_for_report(self, user_id: int) -> List[Dict[str, Any]]:
        """Ошибки за последние 7 дней с примерами для еженедельного отчёта."""
        try:
            cutoff = (datetime.utcnow() - timedelta(days=7)).isoformat()
            response = (self.client.table("error_logs")
                        .select("category, mistake_text, corrected_text")
                        .eq("user_id", user_id)
                        .gte("created_at", cutoff)
                        .execute())
            if not response.data:
                return []
            # Группируем по категории
            grouped: Dict[str, Dict] = {}
            for row in response.data:
                cat = row.get("category", "other")
                if cat.lower() == "none":
                    continue
                if cat not in grouped:
                    grouped[cat] = {"category": cat, "examples": []}
                if row.get("mistake_text"):
                    grouped[cat]["examples"].append(row["mistake_text"])
            return list(grouped.values())
        except Exception as e:
            logger.error(f"Error getting weekly errors: {e}")
            return []


db = SupabaseDB()
