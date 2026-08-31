# CHANGELOG: 2026-07-16
# - get_or_create_user: добавлены поля user_name, english_name, learning_goal, subscription_plan, subscription_expires_at, daily_messages_used, daily_messages_reset_date, synonym_streak_enabled
# - Новые методы: set_user_name, check_message_limit, increment_daily_messages, activate_subscription, complete_onboarding, get_user_message_count

import logging
from typing import Optional, Dict, Any, List
from datetime import datetime, timezone, timedelta
from supabase import create_client, Client

from src.config import settings, ADMIN_IDS, TRIAL_PLAN, TRIAL_DAYS

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
                self.client.table("users").update(
                    {"last_active": datetime.utcnow().isoformat()}
                ).eq("telegram_id", telegram_id).execute()
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
                "recasting_enabled": False,
                "mistakes_practice_enabled": False,
                "last_notified_at": None,
                "reengagement_count": 0,
                "session_count": 0,
                "last_active": datetime.utcnow().isoformat(),
                # Онбординг v2
                "user_name": None,
                "english_name": None,
                "learning_goal": None,
                "onboarding_completed": False,
                # Подписка — новый юзер сразу получает триал
                "subscription_plan": TRIAL_PLAN,
                "subscription_expires_at": (
                    datetime.utcnow() + timedelta(days=TRIAL_DAYS)
                ).isoformat(),
                # Лимиты сообщений
                "daily_messages_used": 0,
                "daily_messages_reset_date": datetime.utcnow().date().isoformat(),
                # Synonym Streak
                "synonym_streak_enabled": False,
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

    async def toggle_recasting(self, telegram_id: int) -> bool:
        """Переключатель Recasting Mode для PenFriend."""
        user = await self.get_or_create_user(telegram_id)
        new_val = not user.get("recasting_enabled", False)
        await self.update_user(telegram_id, {"recasting_enabled": new_val})
        return new_val

    async def toggle_mistakes_practice(self, telegram_id: int) -> bool:
        """Переключатель Mistakes Practice во всех режимах."""
        user = await self.get_or_create_user(telegram_id)
        new_val = not user.get("mistakes_practice_enabled", False)
        await self.update_user(telegram_id, {"mistakes_practice_enabled": new_val})
        return new_val

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
        """
        Возвращает юзеров которым нужно отправить re-engagement.
        Интервал зависит от reengagement_count:
          0 → 24ч с last_active
          1 → 48ч с last_notified_at
          2 → 72ч с last_notified_at
          3+ → никогда
        """
        try:
            response = (self.client.table("users")
                        .select("*")
                        .eq("notifications_enabled", True)
                        .lt("reengagement_count", 3)
                        .execute())
            candidates = response.data or []
            now = datetime.utcnow()
            result = []
            for u in candidates:
                count = u.get("reengagement_count", 0) or 0
                if count == 0:
                    # Первое: 24ч с last_active
                    ref_str = u.get("last_active")
                    hours_needed = 24
                else:
                    # Второе/третье: 48/72ч с last_notified_at
                    ref_str = u.get("last_notified_at")
                    hours_needed = 48 if count == 1 else 72
                if not ref_str:
                    continue
                try:
                    ref_dt = datetime.fromisoformat(ref_str.replace("Z", "+00:00")).replace(tzinfo=None)
                except Exception:
                    continue
                elapsed = (now - ref_dt).total_seconds() / 3600
                # Также проверяем что юзер не заходил с тех пор
                last_active_str = u.get("last_active", "")
                if last_active_str and count > 0:
                    try:
                        last_active_dt = datetime.fromisoformat(last_active_str.replace("Z", "+00:00")).replace(tzinfo=None)
                        if last_active_dt > ref_dt:
                            # Юзер зашёл после последнего уведомления — сбрасываем
                            continue
                    except Exception:
                        pass
                if elapsed >= hours_needed:
                    result.append(u)
            return result
        except Exception as e:
            logger.error(f"Error getting users for notification: {e}")
            return []

    async def mark_user_notified(self, telegram_id: int) -> bool:
        user = await self.get_or_create_user(telegram_id)
        new_count = (user.get("reengagement_count", 0) or 0) + 1
        return await self.update_user(telegram_id, {
            "last_notified_at": datetime.utcnow().isoformat(),
            "reengagement_count": new_count,
        })

    async def reset_reengagement(self, telegram_id: int) -> bool:
        """Сбрасываем счётчик когда юзер вернулся."""
        return await self.update_user(telegram_id, {
            "reengagement_count": 0,
            "last_notified_at": None,
        })

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

            now = datetime.now(timezone.utc)
            start_of_week = (now - timedelta(days=now.weekday())).isoformat()
            week_msgs_res = (self.client.table("messages")
                             .select("id", count="exact")
                             .eq("user_id", telegram_id)
                             .gte("created_at", start_of_week)
                             .execute())

            return {
                "user": user,
                "msgs_total": msgs_res.count or 0,
                "msgs_week": week_msgs_res.count or 0,
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
            response = (self.client.table("summaries")
                        .select("id", count="exact")
                        .eq("user_id", user_id)
                        .eq("is_merged", False)
                        .execute())
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

            msgs_this = (self.client.table("messages")
                         .select("id", count="exact")
                         .eq("user_id", telegram_id)
                         .eq("role", "user")
                         .gte("created_at", start_of_week)
                         .execute())
            msgs_prev = (self.client.table("messages")
                         .select("id", count="exact")
                         .eq("user_id", telegram_id)
                         .eq("role", "user")
                         .gte("created_at", prev_week_start)
                         .lt("created_at", start_of_week)
                         .execute())

            errors_this = (self.client.table("error_logs")
                           .select("category")
                           .eq("user_id", telegram_id)
                           .gte("created_at", start_of_week)
                           .execute())
            errors_prev = (self.client.table("error_logs")
                           .select("category")
                           .eq("user_id", telegram_id)
                           .gte("created_at", prev_week_start)
                           .lt("created_at", start_of_week)
                           .execute())

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
            }
        except Exception as e:
            logger.error(f"Error getting user stats: {e}")
            return {"user": {"level": "unknown", "persona": "greg", "streak_days": 0}}

    # ─── Flow Mode: тихий сбор ошибок для Deep Dive ─────────────────────────

    async def log_flow_error(self, user_id: int, error_data: Dict[str, str]) -> bool:
        """
        Тихо записывает ошибку из Flow Mode в БД.
        Используется фоновой задачей — юзер ничего не видит.
        source="flow" позволяет отделить их от Tutor Mode при формировании отчёта.
        """
        try:
            category = error_data.get("category", "other")
            if not category or category.lower() == "none":
                return False

            self.client.table("error_logs").insert({
                "user_id": user_id,
                "category": category,
                "mistake_text": error_data.get("mistake_text", ""),
                "corrected_text": error_data.get("corrected_text", ""),
                "source": "flow",
            }).execute()
            return True
        except Exception as e:
            logger.error(f"Error logging flow error: {e}")
            return False

    async def log_tutor_error(self, user_id: int, error_data: Dict[str, str]) -> bool:
        """
        Записывает ошибку из Tutor Mode.
        source="tutor" — видна юзеру в статистике.
        """
        try:
            category = error_data.get("category", "other")
            if not category or category.lower() == "none":
                return False

            self.client.table("error_logs").insert({
                "user_id": user_id,
                "category": category,
                "mistake_text": error_data.get("mistake_text", ""),
                "corrected_text": error_data.get("corrected_text", ""),
                "source": "tutor",
            }).execute()
            return True
        except Exception as e:
            logger.error(f"Error logging tutor error: {e}")
            return False

    async def get_top_error_categories(self, user_id: int, limit: int = 2) -> List[str]:
        """Топ категорий ошибок для подсказки модели в Flow Mode."""
        try:
            response = (self.client.table("error_logs")
                        .select("category")
                        .eq("user_id", user_id)
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
        """
        Все ошибки за последние 7 дней (Flow + Tutor), сгруппированные по категории.
        Используется для Sunday Deep Dive и Deep Dive в /stats.
        """
        try:
            cutoff = (datetime.utcnow() - timedelta(days=7)).isoformat()
            response = (self.client.table("error_logs")
                        .select("category, mistake_text, corrected_text")
                        .eq("user_id", user_id)
                        .gte("created_at", cutoff)
                        .execute())
            if not response.data:
                return []

            grouped: Dict[str, Dict] = {}
            for row in response.data:
                cat = row.get("category", "other")
                if not cat or cat.lower() == "none":
                    continue
                if cat not in grouped:
                    grouped[cat] = {"category": cat, "examples": [], "corrected_examples": []}
                if row.get("mistake_text"):
                    grouped[cat]["examples"].append(row["mistake_text"])
                    grouped[cat]["corrected_examples"].append(row.get("corrected_text") or "")

            return list(grouped.values())
        except Exception as e:
            logger.error(f"Error getting weekly errors: {e}")
            return []

    async def get_users_for_sunday_report(self) -> List[Dict[str, Any]]:
        """
        Все пользователи с включёнными уведомлениями — для воскресной рассылки.
        """
        try:
            response = (self.client.table("users")
                        .select("*")
                        .eq("notifications_enabled", True)
                        .execute())
            return response.data or []
        except Exception as e:
            logger.error(f"Error getting users for sunday report: {e}")
            return []


    async def get_recent_errors(self, user_id: int, limit: int = 10) -> List[Dict[str, Any]]:
        """
        Топ-N самых свежих ошибок юзера (по дате создания).
        Используется для Mistakes Practice во всех режимах.
        """
        try:
            response = (self.client.table("error_logs")
                        .select("id, category, mistake_text, corrected_text, mastery_score")
                        .eq("user_id", user_id)
                        .neq("category", "none")
                        .order("created_at", desc=True)
                        .limit(limit)
                        .execute())
            return response.data or []
        except Exception as e:
            logger.error(f"Error getting recent errors: {e}")
            return []

    # ─── Онбординг ─────────────────────────────────────────────────────────

    async def complete_onboarding(self, telegram_id: int) -> bool:
        """Помечает онбординг как завершённый."""
        return await self.update_user(telegram_id, {"onboarding_completed": True})

    # ─── Счётчик сообщений для автооценки уровня ───────────────────────────

    async def get_user_message_count(self, user_id: int) -> int:
        """
        Возвращает общее количество сообщений пользователя (role='user').
        Используется для триггера автооценки каждые 10 сообщений.
        """
        try:
            response = (self.client.table("messages")
                        .select("id", count="exact")
                        .eq("user_id", user_id)
                        .eq("role", "user")
                        .execute())
            return response.count or 0
        except Exception as e:
            logger.error(f"Error getting message count for user {user_id}: {e}")
            return 0


    # ─── Онбординг v2 ──────────────────────────────────────────────────────────

    async def set_user_name(self, telegram_id: int, user_name: str, english_name: str) -> bool:
        return await self.update_user(telegram_id, {
            "user_name": user_name,
            "english_name": english_name,
        })

    async def set_learning_goal(self, telegram_id: int, goal: str) -> bool:
        return await self.update_user(telegram_id, {"learning_goal": goal})

    async def complete_onboarding(self, telegram_id: int) -> bool:
        return await self.update_user(telegram_id, {"onboarding_completed": True})

    # ─── Лимиты сообщений ──────────────────────────────────────────────────────

    async def check_message_limit(self, telegram_id: int) -> dict:
        """
        Проверяет лимит сообщений пользователя.
        Возвращает: {"allowed": bool, "used": int, "limit": int, "plan": str}
        """
        try:
            from src.config import get_daily_message_limit
            user = await self.get_or_create_user(telegram_id)
            if await self.check_subscription_expired(telegram_id):
                user["subscription_plan"] = "free"
            plan = user.get("subscription_plan", "free")
            limit = get_daily_message_limit(plan)
            today = datetime.utcnow().date().isoformat()
            reset_date = user.get("daily_messages_reset_date", today)

            # Сброс счётчика если новый день
            if reset_date != today:
                await self.update_user(telegram_id, {
                    "daily_messages_used": 0,
                    "daily_messages_reset_date": today,
                })
                used = 0
            else:
                used = user.get("daily_messages_used", 0)

            # 0 = безлимит (pro)
            allowed = (limit == 0) or (used < limit)
            return {"allowed": allowed, "used": used, "limit": limit, "plan": plan}
        except Exception as e:
            logger.error(f"Error checking message limit: {e}")
            return {"allowed": True, "used": 0, "limit": 0, "plan": "free"}

    async def increment_daily_messages(self, telegram_id: int) -> bool:
        """Увеличивает счётчик использованных сообщений за день."""
        try:
            user = await self.get_or_create_user(telegram_id)
            used = user.get("daily_messages_used", 0)
            return await self.update_user(telegram_id, {"daily_messages_used": used + 1})
        except Exception as e:
            logger.error(f"Error incrementing daily messages: {e}")
            return False

    # ─── Подписка ──────────────────────────────────────────────────────────────

    async def activate_subscription(self, telegram_id: int, plan: str, period: str) -> bool:
        """
        Активирует подписку после успешной оплаты Stars.
        period: "2weeks" или "month"
        """
        try:
            from datetime import timedelta
            now = datetime.utcnow()
            if period == "2weeks":
                expires = now + timedelta(days=14)
            else:
                expires = now + timedelta(days=30)
            return await self.update_user(telegram_id, {
                "subscription_plan": plan,
                "subscription_expires_at": expires.isoformat(),
            })
        except Exception as e:
            logger.error(f"Error activating subscription: {e}")
            return False

    async def check_subscription_expired(self, telegram_id: int) -> bool:
        """Проверяет и сбрасывает истёкшую подписку. Возвращает True если сброс произошёл."""
        try:
            user = await self.get_or_create_user(telegram_id)
            expires_str = user.get("subscription_expires_at")
            plan = user.get("subscription_plan", "free")
            if plan == "free" or not expires_str:
                return False
            expires = datetime.fromisoformat(expires_str)
            if datetime.utcnow() > expires:
                await self.update_user(telegram_id, {
                    "subscription_plan": "free",
                    "subscription_expires_at": None,
                })
                return True
            return False
        except Exception as e:
            logger.error(f"Error checking subscription: {e}")
            return False

    # ─── Счётчик сообщений для автооценки уровня ───────────────────────────────

    async def get_user_message_count(self, user_id: int) -> int:
        try:
            response = (self.client.table("messages")
                        .select("id", count="exact")
                        .eq("user_id", user_id)
                        .eq("role", "user")
                        .execute())
            return response.count or 0
        except Exception as e:
            logger.error(f"Error getting message count: {e}")
            return 0


db = SupabaseDB()
