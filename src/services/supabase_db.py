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
                # Update last_active on fetch
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
                "vocab_practice_enabled": True,
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

    async def toggle_vocab_practice_mode(self, telegram_id: int) -> Optional[bool]:
        try:
            user = await self.get_or_create_user(telegram_id)
            current_state = user.get("vocab_practice_enabled", True)
            new_state = not current_state
            await self.update_user(telegram_id, {"vocab_practice_enabled": new_state})
            return new_state
        except Exception as e:
            logger.error(f"Error toggling vocab practice: {e}")
            return None

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
        """Собирает полную статистику для админ-панели средствами Python для надежности"""
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
                    # Parse assuming ISO format, remove Z if present
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
            
            mode_ranking = sorted(modes.items(), key=lambda x: x[1], reverse=True)
            top_personas = sorted(personas.items(), key=lambda x: x[1], reverse=True)[:5]
            
            return {
                "total": len(users),
                "new_today": new_today,
                "new_week": new_week,
                "active_week": active_week,
                "mode_ranking": mode_ranking,
                "top_personas": top_personas
            }
        except Exception as e:
            logger.error(f"Error getting admin stats: {e}")
            return {
                "total": 0, "new_today": 0, "new_week": 0, "active_week": 0,
                "mode_ranking": [], "top_personas": []
            }

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
            vocab_res = self.client.table("vocabulary").select("id", count="exact").eq("user_id", telegram_id).execute()
            mastered_res = self.client.table("vocabulary").select("id", count="exact").eq("user_id", telegram_id).gte("mastery_score", 5).execute()
            
            now = datetime.now(timezone.utc)
            start_of_week = (now - timedelta(days=now.weekday())).isoformat()
            week_msgs_res = self.client.table("messages").select("id", count="exact").eq("user_id", telegram_id).gte("created_at", start_of_week).execute()
            
            return {
                "user": user,
                "msgs_total": msgs_res.count or 0,
                "msgs_week": week_msgs_res.count or 0,
                "vocab_count": vocab_res.count or 0,
                "mastered_count": mastered_res.count or 0
            }
        except Exception as e:
            logger.error(f"Error getting user card: {e}")
            return None

    # ─── История сообщений ──────────────────────────────────────────────────

    async def save_message(self, user_id: int, role: str, content: str, tokens: int = 0):
        try:
            data = {
                "user_id": user_id,
                "role": role,
                "content": content,
            }
            self.client.table("messages").insert(data).execute()
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

    async def save_summary(self, user_id: int, content: str, is_merged: bool = False):
        try:
            self.client.table("summaries").insert({
                "user_id": user_id,
                "content": content,
                "is_merged": is_merged
            }).execute()
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
        if not summary_ids: return True
        try:
            for sid in summary_ids:
                self.client.table("summaries").update({"is_merged": True}).eq("id", sid).execute()
            return True
        except Exception as e:
            logger.error(f"Error marking summaries as merged: {e}")
            return False

    # ─── Статистика и Ошибки ───────────────────────────────────────────────

    async def get_user_stats(self, telegram_id: int) -> Dict[str, Any]:
        try:
            user = await self.get_or_create_user(telegram_id)
            vocab_res = self.client.table("vocabulary").select("id", count="exact").eq("user_id", telegram_id).execute()
            mastered_res = self.client.table("vocabulary").select("id", count="exact").eq("user_id", telegram_id).gte("mastery_score", 5).execute()
            
            now = datetime.now(timezone.utc)
            start_of_week = (now - timedelta(days=now.weekday())).isoformat()
            prev_week_start = (now - timedelta(days=now.weekday() + 7)).isoformat()
            
            msgs_this = self.client.table("messages").select("id", count="exact").eq("user_id", telegram_id).gte("created_at", start_of_week).execute()
            msgs_prev = self.client.table("messages").select("id", count="exact").eq("user_id", telegram_id).gte("created_at", prev_week_start).lt("created_at", start_of_week).execute()
            
            errors_this = self.client.table("errors").select("category").eq("user_id", telegram_id).gte("created_at", start_of_week).execute()
            errors_prev = self.client.table("errors").select("category").eq("user_id", telegram_id).gte("created_at", prev_week_start).lt("created_at", start_of_week).execute()
            
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
                "vocabulary_count": vocab_res.count or 0,
                "mastered_count": mastered_res.count or 0,
                "msgs_this_week": msgs_this.count or 0,
                "msgs_prev_week": msgs_prev.count or 0,
                "error_stats_week": error_stats_week,
                "error_stats_prev_week": error_stats_prev
            }
        except Exception as e:
            logger.error(f"Error getting user stats: {e}")
            return {"user": {"level": "unknown", "persona": "greg", "streak_days": 0}}

async def log_error(self, user_id: int, error_data: Dict[str, str]) -> bool:
        try:
            data = {
                "user_id": user_id,
                "category": error_data.get("category", "other"),
                "mistake_text": error_data.get("mistake_text", ""),
                "corrected_text": error_data.get("corrected_text", ""),
                "source": error_data.get("source", "tutor")
            }
            self.client.table("errors").insert(data).execute()
            return True
        except Exception as e:
            logger.error(f"Error logging error: {e}")
            return False

    async def get_top_error_categories(self, user_id: int, limit: int = 2) -> List[str]:
        try:
            response = self.client.table("errors").select("category").eq("user_id", user_id).execute()
            if not response.data: return []
            
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

    # ─── Словарь ────────────────────────────────────────────────────────────

    async def add_to_vocabulary(self, user_id: int, item: Dict[str, str]) -> bool:
        try:
            word = item.get("word_or_phrase", "").strip().lower()
            if not word: return False
            
            existing = self.client.table("vocabulary").select("id").eq("user_id", user_id).ilike("word_or_phrase", word).execute()
            if existing.data: return False
            
            data = {
                "user_id": user_id,
                "word_or_phrase": item.get("word_or_phrase", ""),
                "translation": item.get("translation", ""),
                "context_sentence": item.get("context_sentence", ""),
                "word_type": item.get("word_type", "word"),
                "mastery_score": 0,
                "times_reminded": 0,
                "times_used": 0
            }
            self.client.table("vocabulary").insert(data).execute()
            return True
        except Exception as e:
            logger.error(f"Error adding to vocabulary: {e}")
            return False

    async def get_user_vocabulary(self, user_id: int, tab: str = "active", limit: int = 20) -> List[Dict[str, Any]]:
        try:
            query = self.client.table("vocabulary").select("*").eq("user_id", user_id)
            if tab == "active":
                query = query.lt("mastery_score", 5).order("times_reminded", desc=False)
            elif tab == "mastered":
                query = query.gte("mastery_score", 5)
            elif tab == "difficult":
                query = query.lt("mastery_score", 3).gte("times_reminded", 3)
            
            response = query.order("created_at", desc=True).limit(limit).execute()
            return response.data or []
        except Exception as e:
            logger.error(f"Error getting vocabulary: {e}")
            return []

    async def find_word_in_text(self, user_id: int, text: str) -> Optional[Dict[str, Any]]:
        try:
            words = await self.get_user_vocabulary(user_id, tab="active", limit=50)
            text_lower = text.lower()
            for w in words:
                phrase = w.get("word_or_phrase", "").lower()
                if phrase and phrase in text_lower:
                    return w
            return None
        except Exception as e:
            logger.error(f"Error finding word: {e}")
            return None

    async def increase_mastery(self, word_id: int) -> int:
        try:
            word_res = self.client.table("vocabulary").select("mastery_score, times_used").eq("id", word_id).execute()
            if not word_res.data: return 0
            
            current_score = word_res.data[0].get("mastery_score", 0)
            times_used = word_res.data[0].get("times_used", 0)
            
            new_score = min(5, current_score + 1)
            self.client.table("vocabulary").update({
                "mastery_score": new_score,
                "times_used": times_used + 1
            }).eq("id", word_id).execute()
            
            return new_score
        except Exception as e:
            logger.error(f"Error increasing mastery: {e}")
            return 0

    async def increment_vocab_remind_counter(self, telegram_id: int) -> int:
        try:
            user = await self.get_or_create_user(telegram_id)
            new_val = user.get("vocab_remind_counter", 0) + 1
            await self.update_user(telegram_id, {"vocab_remind_counter": new_val})
            return new_val
        except Exception as e:
            logger.error(f"Error incrementing vocab remind counter: {e}")
            return 0

    async def reset_vocab_remind_counter(self, telegram_id: int) -> bool:
        return await self.update_user(telegram_id, {"vocab_remind_counter": 0})

    async def get_word_for_reminder(self, user_id: int) -> Optional[Dict[str, Any]]:
        try:
            response = (self.client.table("vocabulary")
                        .select("*")
                        .eq("user_id", user_id)
                        .lt("mastery_score", 5)
                        .order("times_reminded", desc=False)
                        .limit(1)
                        .execute())
            if response.data:
                return response.data[0]
            return None
        except Exception as e:
            logger.error(f"Error getting word for reminder: {e}")
            return None

    async def mark_word_reminded(self, word_id: int) -> bool:
        try:
            word_res = self.client.table("vocabulary").select("times_reminded").eq("id", word_id).execute()
            if not word_res.data: return False
            
            current = word_res.data[0].get("times_reminded", 0)
            self.client.table("vocabulary").update({"times_reminded": current + 1}).eq("id", word_id).execute()
            return True
        except Exception as e:
            logger.error(f"Error marking word reminded: {e}")
            return False
    async def get_flow_users_for_weekly_report(self) -> List[Dict[str, Any]]:
        """Пользователи, у которых есть Flow-ошибки за последние 7 дней и которые еще не получали отчёт."""
        try:
            cutoff = (datetime.utcnow() - timedelta(days=7)).isoformat()
            
            # Ищем уникальных пользователей с ошибками source='flow' за неделю
            response = (self.client.table("errors")
                        .select("user_id")
                        .eq("source", "flow")
                        .gte("created_at", cutoff)
                        .execute())
            if not response.data:
                return []
                
            user_ids = list({row["user_id"] for row in response.data})
            
            users_resp = (self.client.table("users")
                          .select("telegram_id, persona, weekly_report_sent_at")
                          .in_("telegram_id", user_ids)
                          .execute())
            
            if not users_resp.data:
                return []
                
            # Фильтруем тех, кому уже отправляли отчет на этой неделе
            result = []
            week_ago = datetime.utcnow() - timedelta(days=7)
            for u in users_resp.data:
                sent_at = u.get("weekly_report_sent_at")
                if sent_at:
                    try:
                        sent_dt = datetime.fromisoformat(sent_at.replace("Z", "+00:00")).replace(tzinfo=None)
                        if sent_dt > week_ago:
                            continue
                    except Exception:
                        pass
                result.append(u)
            return result
        except Exception as e:
            logger.error(f"Error getting flow users for report: {e}")
            return []

    async def get_flow_errors_for_report(self, user_id: int) -> List[Dict[str, Any]]:
        """Получает топ-3 ошибки Flow Mode за неделю для конкретного юзера."""
        try:
            cutoff = (datetime.utcnow() - timedelta(days=7)).isoformat()
            response = (self.client.table("errors")
                        .select("category, mistake_text, corrected_text")
                        .eq("user_id", user_id)
                        .eq("source", "flow")
                        .gte("created_at", cutoff)
                        .execute())
            if not response.data:
                return []
                
            grouped: Dict[str, Dict] = {}
            for row in response.data:
                cat = row.get("category", "other")
                if cat.lower() == "none": continue
                if cat not in grouped:
                    grouped[cat] = {
                        "category": cat,
                        "original": row.get("mistake_text", ""),
                        "corrected": row.get("corrected_text", ""),
                        "count": 0,
                    }
                grouped[cat]["count"] += 1
                
            # Сортируем по частоте и берем топ-3
            sorted_errors = sorted(grouped.values(), key=lambda x: x["count"], reverse=True)
            return sorted_errors[:3]
        except Exception as e:
            logger.error(f"Error getting flow errors for report: {e}")
            return []

    async def mark_weekly_report_sent(self, user_id: int) -> None:
        """Отмечает время отправки воскресного отчета."""
        try:
            self.client.table("users").update({
                "weekly_report_sent_at": datetime.utcnow().isoformat()
            }).eq("telegram_id", user_id).execute()
        except Exception as e:
            logger.error(f"Error marking weekly report sent: {e}")
    async def get_flow_users_for_weekly_report(self) -> List[Dict[str, Any]]:
        """Пользователи, у которых есть Flow-ошибки за последние 7 дней и которые еще не получали отчёт."""
        try:
            cutoff = (datetime.utcnow() - timedelta(days=7)).isoformat()
            
            # Ищем уникальных пользователей с ошибками source='flow' за неделю
            response = (self.client.table("errors")
                        .select("user_id")
                        .eq("source", "flow")
                        .gte("created_at", cutoff)
                        .execute())
            if not response.data:
                return []
                
            user_ids = list({row["user_id"] for row in response.data})
            
            users_resp = (self.client.table("users")
                          .select("telegram_id, persona, weekly_report_sent_at")
                          .in_("telegram_id", user_ids)
                          .execute())
            
            if not users_resp.data:
                return []
                
            # Фильтруем тех, кому уже отправляли отчет на этой неделе
            result = []
            week_ago = datetime.utcnow() - timedelta(days=7)
            for u in users_resp.data:
                sent_at = u.get("weekly_report_sent_at")
                if sent_at:
                    try:
                        sent_dt = datetime.fromisoformat(sent_at.replace("Z", "+00:00")).replace(tzinfo=None)
                        if sent_dt > week_ago:
                            continue
                    except Exception:
                        pass
                result.append(u)
            return result
        except Exception as e:
            logger.error(f"Error getting flow users for report: {e}")
            return []

    async def get_flow_errors_for_report(self, user_id: int) -> List[Dict[str, Any]]:
        """Получает топ-3 ошибки Flow Mode за неделю для конкретного юзера."""
        try:
            cutoff = (datetime.utcnow() - timedelta(days=7)).isoformat()
            response = (self.client.table("errors")
                        .select("category, mistake_text, corrected_text")
                        .eq("user_id", user_id)
                        .eq("source", "flow")
                        .gte("created_at", cutoff)
                        .execute())
            if not response.data:
                return []
                
            grouped: Dict[str, Dict] = {}
            for row in response.data:
                cat = row.get("category", "other")
                if cat.lower() == "none": continue
                if cat not in grouped:
                    grouped[cat] = {
                        "category": cat,
                        "original": row.get("mistake_text", ""),
                        "corrected": row.get("corrected_text", ""),
                        "count": 0,
                    }
                grouped[cat]["count"] += 1
                
            # Сортируем по частоте и берем топ-3
            sorted_errors = sorted(grouped.values(), key=lambda x: x["count"], reverse=True)
            return sorted_errors[:3]
        except Exception as e:
            logger.error(f"Error getting flow errors for report: {e}")
            return []

    async def mark_weekly_report_sent(self, user_id: int) -> None:
        """Отмечает время отправки воскресного отчета."""
        try:
            self.client.table("users").update({
                "weekly_report_sent_at": datetime.utcnow().isoformat()
            }).eq("telegram_id", user_id).execute()
        except Exception as e:
            logger.error(f"Error marking weekly report sent: {e}")
db = SupabaseDB()
