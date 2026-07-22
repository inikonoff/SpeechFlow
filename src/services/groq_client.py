# CHANGELOG: 2026-07-16
# - suggest_english_name: новый метод — предлагает английский эквивалент имени (Илья→Elijah)
# - generate_persona_greeting: добавлен параметр user_name — персонаж обращается по имени
# - Все модели заменены с llama-4-scout-17b-16e-instruct на qwen/qwen3.6-27b

# CHANGELOG: 2026-04-12
# - generate_penfriend_multibubble: response_format json_object возвращён
# - generate_penfriend_multibubble: новый формат JSON — has_error + correct_word + messages
# - correct_word передаётся через маркер __RECAST__ в первом сообщении
# - message.py обрабатывает маркер и выделяет correct_word bold самостоятельно
import random
import asyncio
import logging
import json
import re
from typing import List, Optional, Dict, Any, Tuple
from openai import AsyncOpenAI

from src.config import settings
from src.personas import get_persona_prompt, get_persona_tutor_prompt

logger = logging.getLogger(__name__)

# ─── Recasting block — вставляется в промпт PenFriend когда recasting включён ──

MISTAKES_PRACTICE_PASSIVE = (
    "# MISTAKES PRACTICE (passive)\n"
    "One of this person's recent errors: {category} — "
    "they said \"{mistake}\", correct form is \"{corrected}\".\n"
    "Once during this response, naturally use the correct form in your own speech. "
    "Do NOT announce it. Do NOT say 'by the way'. Just model it casually. "
    "If it doesn't fit the conversation — skip it entirely."
)

MISTAKES_PRACTICE_ACTIVE = (
    "# MISTAKES PRACTICE (active — Tutor only)\n"
    "One of this student's recent errors: {category} — "
    "they said \"{mistake}\", correct form is \"{corrected}\".\n"
    "At a natural moment, ask a question that organically leads the student "
    "to use the correct construction themselves — without telling them what to say. "
    "Do NOT say 'try using X' or 'repeat after me'. Just steer the conversation toward it. "
    "Example: if the error is 'go to school' — ask 'What time do you usually get to school?' "
    "Only do this ONCE. If it does not fit naturally — skip it entirely."
)

RECASTING_BLOCK = """
# CRITICAL INSTRUCTION: RECASTING MODE
The user is learning English. You MUST implicitly correct their biggest grammar/vocabulary error.
1. Do NOT say "you made a mistake".
2. Use the CORRECTED phrase naturally in your response as if it's your own words.
3. You MUST wrap ONLY the corrected phrase in **double asterisks**.
If the user's English is completely correct — do not use asterisks.

Example:
User: "Yesterday I go to store"
You: "Oh nice, **you went to the store** — did you find what you needed?"
"""


class GroqClient:
    def __init__(self, api_keys: List[str]):
        self.clients = []
        self.current_index = 0
        self._lock = asyncio.Lock()

        for key in api_keys:
            if key.strip():
                self.clients.append(
                    AsyncOpenAI(
                        api_key=key.strip(),
                        base_url="https://api.groq.com/openai/v1",
                        timeout=60.0
                    )
                )
        logger.info(f"✅ Инициализировано {len(self.clients)} Groq клиентов")

    async def _get_next_client(self) -> Optional[AsyncOpenAI]:
        if not self.clients:
            return None
        async with self._lock:
            client = self.clients[self.current_index]
            self.current_index = (self.current_index + 1) % len(self.clients)
            return client

    async def _make_request(self, func, *args, **kwargs):
        if not self.clients:
            raise Exception("Нет доступных Groq клиентов")

        errors = []
        for attempt in range(len(self.clients) * 2):
            client = await self._get_next_client()
            if not client:
                break
            try:
                return await func(client, *args, **kwargs)
            except Exception as e:
                errors.append(str(e))
                logger.warning(f"❌ Groq request failed (attempt {attempt + 1}): {e}")
                await asyncio.sleep(0.5 + random.random())

        raise Exception(f"Все Groq клиенты недоступны: {'; '.join(errors[:3])}")

    # ─── Транскрибация ─────────────────────────────────────────────────────

    async def transcribe_audio(self, audio_bytes: bytes) -> Optional[str]:
        async def _transcribe(client):
            response = await client.audio.transcriptions.create(
                model="whisper-large-v3",
                file=("voice.ogg", audio_bytes, "audio/ogg"),
                language="en",
                response_format="text",
                temperature=0.0,
                prompt=(
                    "Wait — are you serious? She looked at him and said: 'I've already left,' "
                    "but he replied, 'No, you haven't — not yet.' "
                    "It was John, her oldest friend, who finally spoke: 'Well, that's that.' "
                )
            )
            return response

        try:
            result = await self._make_request(_transcribe)
            if isinstance(result, str):
                return result.strip()
            elif hasattr(result, 'text'):
                return result.text.strip()
            else:
                return str(result).strip()
        except Exception as e:
            logger.error(f"❌ Ошибка транскрибации: {e}")
            return None

    # ─── Коррекция (Tutor Mode) ────────────────────────────────────────────

    async def correct_text(self, text: str, level: str) -> Dict[str, Any]:
        """
        Анализирует текст и находит ОДНУ самую важную ошибку.
        Используется в Tutor Mode (видимая карточка) и Flow Mode (тихий фон).
        """
        system_prompt = f"""# ROLE
You are an expert English linguist analyzing spoken English for a language tutor app.
Your job: find the ONE most important language error worth correcting.

# STRICT IGNORE LIST (never flag these)
- Missing punctuation or capitalization
- Conversational fillers: umm, ah, like, you know
- Minor speech-to-text artifacts
- Informal contractions or casual phrasing
- Anything a native speaker would say in casual conversation

# ONLY FLAG
- Wrong verb tenses (I go there yesterday)
- Incorrect prepositions (I am on the intermediate level)
- Wrong verb forms (I am look forward to see you)
- Wrong noun forms (I bought two apple yesterday)
- Wrong forms of adjectives (This the gooddest movie I have ever seen)
- Glaring vocabulary misuse

# LEVEL CONTEXT
User Level: {level}
Beginners: only flag errors that make meaning unclear.
Advanced: flag subtle but real errors (wrong preposition, wrong tense aspect).

# EXPLANATION STYLE
- Write the explanation in Russian.
- Max 2 short sentences. Focus on WHY it is wrong. Be friendly, not academic.
- Example: "Глагол 'look forward to' всегда требует герундий. Правильно: 'looking forward to seeing'."

# OUTPUT FORMAT (JSON ONLY, no markdown)
{{
  "corrected_sentence": "[Full corrected sentence. If no error, return original text unchanged.]",
  "explanation": "[Explanation in Russian. Empty string if no error.]",
  "error_category": "grammar|vocabulary|prepositions|structure|none"
}}"""

        async def _correct(client):
            response = await client.chat.completions.create(
                model="qwen/qwen3.6-27b",
                extra_body={"reasoning_format": "none"},",
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": f"USER TEXT: {text}\n\nAnalyze and correct."}
                ],
                temperature=0.0,
                response_format={"type": "json_object"}
            )
            return response.choices[0].message.content

        try:
            result = await self._make_request(_correct)
            match = re.search(r'{.*}', result, re.DOTALL)
            clean_json = match.group(0) if match else result
            return json.loads(clean_json)
        except Exception as e:
            logger.error(f"❌ Ошибка коррекции: {e}")
            return {
                "corrected_sentence": text,
                "explanation": "",
                "error_category": "none"
            }

    # ─── Flow Mode: параллельный анализ ошибок (тихий фон) ────────────────

    async def check_flow_errors(self, text: str, level: str) -> Dict[str, Any]:
        """
        Запускается как asyncio.create_task во Flow Mode.
        Юзер не видит результат — ошибки тихо пишутся в БД для Sunday Deep Dive.
        Использует тот же correct_text, просто вызывается в фоне.
        """
        return await self.correct_text(text, level)

    # ─── Flow Mode ─────────────────────────────────────────────────────────

    async def generate_flow_response(
        self,
        text: str,
        persona_key: str,
        history: Optional[List[Dict[str, str]]] = None,
        summary: Optional[str] = None,
        session_count: int = 0,
        top_errors: Optional[List[str]] = None,
        practice_error: Optional[Dict[str, Any]] = None,
    ) -> str:
        persona_prompt = get_persona_prompt(persona_key, session_count=session_count)
        persona_prompt += (
            "\n\n# IMPORTANT: IGNORE PREVIOUS PERSONA"
            "\nPrevious assistant messages in this conversation may be from a DIFFERENT character."
            f"\nYou are {persona_key.upper()}. Do NOT adopt any personality, role, or identity from those messages."
            "\nOnly use the factual topics discussed — not who said them or how they spoke."
        )

        if summary:
            persona_prompt += (
                f"\n\n# WHAT YOU KNOW ABOUT THIS PERSON\n{summary}\n"
                f"Use this naturally, never dump it all at once."
            )

        if top_errors:
            errors_str = ", ".join(top_errors)
            persona_prompt += (
                f"\n\n# LANGUAGE NOTE\n"
                f"This person often struggles with: {errors_str}. "
                f"Occasionally use correct examples of these naturally in your own speech — "
                f"no need to draw attention, just model the right form casually."
            )

        if practice_error and practice_error.get("corrected_text"):
            persona_prompt += "\n\n" + MISTAKES_PRACTICE_PASSIVE.format(
                category=practice_error.get("category", "grammar"),
                mistake=practice_error.get("mistake_text", ""),
                corrected=practice_error["corrected_text"],
            )

        # В Flow Mode нет recasting — запрещаем любой bold
        persona_prompt += "\n\n# FORMATTING\nPlain text only. No asterisks or markdown."

        messages = [{"role": "system", "content": persona_prompt}]
        if history:
            messages.extend(history)
        messages.append({"role": "user", "content": text})

        async def _flow(client):
            response = await client.chat.completions.create(
                model="qwen/qwen3.6-27b",
                extra_body={"reasoning_format": "none"},",
                messages=messages,
                temperature=0.9,
                max_tokens=300
            )
            return response.choices[0].message.content

        try:
            return await self._make_request(_flow)
        except Exception as e:
            logger.error(f"❌ Ошибка Flow генерации: {e}")
            return "Hey, still here. Go on."

    # ─── PenFriend Mode ────────────────────────────────────────────────────

    async def generate_penfriend_response(
        self,
        text: str,
        persona_key: str,
        history: Optional[List[Dict[str, str]]] = None,
        summary: Optional[str] = None,
        recasting_enabled: bool = False,
        practice_error: Optional[Dict[str, Any]] = None,
    ) -> str:
        persona_prompt = get_persona_prompt(persona_key, session_count=10)

        system_prompt = (
            f"{persona_prompt}"
            "\n\n# IMPORTANT: IGNORE PREVIOUS PERSONA"
            "\nPrevious assistant messages in this conversation may be from a DIFFERENT character."
            f"\nYou are {persona_key.upper()}. Do NOT adopt any personality, role, or identity from those messages."
            "\nOnly use the factual topics discussed — not who said them or how they spoke."
            "\n\n# PENFRIEND MODE\n"
            "This is a text-based conversation. You are a real person texting.\n"
            "Keep it natural — casual phrasing, occasional shorthand, good English."
        )

        if summary:
            system_prompt += (
                f"\n\n# WHAT YOU KNOW ABOUT THIS PERSON\n{summary}\n"
                f"Use this naturally, never dump it all at once."
            )

        if recasting_enabled:
            system_prompt += f"\n\n{RECASTING_BLOCK}"

        if practice_error and practice_error.get("corrected_text"):
            system_prompt += "\n\n" + MISTAKES_PRACTICE_PASSIVE.format(
                category=practice_error.get("category", "grammar"),
                mistake=practice_error.get("mistake_text", ""),
                corrected=practice_error["corrected_text"],
            )

        messages = [{"role": "system", "content": system_prompt}]
        if history:
            messages.extend(history)
        messages.append({"role": "user", "content": text})

        async def _penfriend(client):
            response = await client.chat.completions.create(
                model="qwen/qwen3.6-27b",
                extra_body={"reasoning_format": "none"},",
                messages=messages,
                temperature=0.85,
                max_tokens=200
            )
            return response.choices[0].message.content

        try:
            return await self._make_request(_penfriend)
        except Exception as e:
            logger.error(f"PenFriend generation error: {e}")
            return "Ha, interesting. Tell me more."

    # ─── Switch opener ─────────────────────────────────────────────────────

    async def generate_switch_opener(
        self,
        new_persona_key: str,
        previous_summary: str,
        session_count: int = 0
    ) -> str:
        persona_prompt = get_persona_prompt(new_persona_key, session_count=session_count)

        system = (
            f"{persona_prompt}\n\n"
            f"# CONTEXT\n"
            f"The person just switched to talking with you from someone else.\n"
            f"Brief summary of that conversation: {previous_summary}\n\n"
            f"# YOUR TASK\n"
            f"Open the conversation naturally. Reference something from the summary only if it flows organically. "
            f"One or two sentences max. Like running into someone you know a little."
        )

        async def _opener(client):
            response = await client.chat.completions.create(
                model="qwen/qwen3.6-27b",
                extra_body={"reasoning_format": "none"},",
                messages=[
                    {"role": "system", "content": system},
                    {"role": "user", "content": "[start the conversation]"}
                ],
                temperature=0.9,
                max_tokens=100
            )
            return response.choices[0].message.content

        try:
            return await self._make_request(_opener)
        except Exception as e:
            logger.error(f"❌ Ошибка Switch opener: {e}")
            return "Hey. What's up?"

    # ─── Persona greeting ──────────────────────────────────────────────────

    async def generate_persona_greeting(
        self,
        persona_key: str,
        user_level: str,
        session_count: int = 0,
        summary: Optional[str] = None,
        user_name: str = "",
    ) -> str:
        persona_prompt = get_persona_prompt(persona_key, session_count=session_count)

        if summary:
            memory_block = (
                f"\n\n# WHAT YOU KNOW ABOUT THIS PERSON\n{summary}\n"
                f"You've talked before. Open naturally — reference something real, "
                f"don't pretend this is the first time."
            )
        else:
            memory_block = ""

        name_block = f"\n\n# USER NAME\nThe user's name is {user_name}. Use it naturally in your greeting." if user_name else ""

        context_instruction = (
            "[greet the user for the first time — you've never spoken before]"
            if session_count == 0
            else "[you know this person — greet them naturally]"
        )

        system = (
            f"{persona_prompt}{memory_block}{name_block}\n\n"
            f"# YOUR TASK\n"
            f"The person just chose to talk with you. Say hello in your own voice.\n"
            f"One or two sentences. Warm but natural, not over-the-top excited.\n"
            f"NEVER say 'finally', 'I missed you', 'it's so great to see you again'.\n"
            f"End with a simple opening question."
        )

        async def _greeting(client):
            response = await client.chat.completions.create(
                model="qwen/qwen3.6-27b",
                extra_body={"reasoning_format": "none"},",
                messages=[
                    {"role": "system", "content": system},
                    {"role": "user", "content": context_instruction}
                ],
                temperature=0.9,
                max_tokens=100
            )
            return response.choices[0].message.content

        try:
            return await self._make_request(_greeting)
        except Exception as e:
            logger.error(f"❌ Ошибка приветствия персонажа: {e}")
            return "Hey! What's on your mind?"

    # ─── Farewell detection ────────────────────────────────────────────────

    async def detect_farewell(self, text: str) -> bool:
        async def _detect(client):
            response = await client.chat.completions.create(
                model="qwen/qwen3.6-27b",
                extra_body={"reasoning_format": "none"},",
                messages=[
                    {
                        "role": "system",
                        "content": (
                            "You detect farewells in user messages. "
                            "A farewell is any form of goodbye, signing off, or ending the conversation. "
                            "Reply with only 'yes' or 'no'."
                        )
                    },
                    {"role": "user", "content": text}
                ],
                temperature=0.0,
                max_tokens=5
            )
            return response.choices[0].message.content.strip().lower().startswith("yes")

        try:
            return await self._make_request(_detect)
        except Exception as e:
            logger.error(f"❌ Ошибка детектирования прощания: {e}")
            return False

    # ─── Summarization ─────────────────────────────────────────────────────

    async def summarize_conversation(
        self,
        messages: List[Dict[str, str]],
        existing_summary: Optional[str] = None
    ) -> tuple:
        conversation_text = "\n".join([
            f"{m['role'].upper()}: {m['content']}"
            for m in messages
        ])

        if existing_summary:
            context_block = (
                f"EXISTING KNOWLEDGE ABOUT THIS PERSON:\n{existing_summary}\n\n"
                f"NEW CONVERSATION TO SUMMARIZE:\n{conversation_text}"
            )
        else:
            context_block = f"CONVERSATION:\n{conversation_text}"

        async def _summarize(client):
            response = await client.chat.completions.create(
                model="qwen/qwen3.6-27b",
                extra_body={"reasoning_format": "none"},",
                messages=[
                    {
                        "role": "system",
                        "content": (
                            "You analyze a conversation and return a JSON object with two fields.\n"
                            "1. \"summary\": concise facts about the USER in third person, present tense. "
                            "Life, interests, goals, relationships. Never facts about the ASSISTANT. Max 150 words.\n"
                            "2. \"topics_to_discuss\": comma-separated topics the USER has shown interest in. "
                            "Return ONLY valid JSON."
                        )
                    },
                    {"role": "user", "content": context_block}
                ],
                temperature=0.0,
                max_tokens=400
            )
            raw = response.choices[0].message.content.strip()
            try:
                parsed = json.loads(raw)
                return parsed.get("summary", raw), parsed.get("topics_to_discuss", "")
            except Exception:
                return raw, ""

        try:
            return await self._make_request(_summarize)
        except Exception as e:
            logger.error(f"❌ Ошибка саммаризации: {e}")
            return "", ""

    async def merge_summaries(self, summaries: List[str]) -> str:
        combined = "\n\n---\n\n".join(summaries)

        async def _merge(client):
            response = await client.chat.completions.create(
                model="qwen/qwen3.6-27b",
                extra_body={"reasoning_format": "none"},",
                messages=[
                    {
                        "role": "system",
                        "content": (
                            "You merge multiple conversation summaries about the same person into one. "
                            "Keep all relevant facts. Remove duplicates. "
                            "Third person, present tense. Max 200 words."
                        )
                    },
                    {"role": "user", "content": f"SUMMARIES TO MERGE:\n\n{combined}"}
                ],
                temperature=0.0,
                max_tokens=400
            )
            return response.choices[0].message.content.strip()

        try:
            return await self._make_request(_merge)
        except Exception as e:
            logger.error(f"❌ Ошибка схлопывания саммари: {e}")
            return combined

    # ─── Deep Dive (Stats + Sunday) — голос Mrs. Smith ────────────────────

    async def generate_stats_deep_dive(self, stats: dict) -> str:
        """
        Нарративный мотивирующий отчёт по кнопке 'Deep Dive' в /stats.
        Чистая проза, без заголовков и структуры — как на скриншоте.
        LLM анализирует статистику и пишет личный, конкретный текст.
        """
        user = stats.get("user", {})
        level = str(user.get("level", "unknown")).upper()
        streak = user.get("streak_days", 0)
        msgs_this = stats.get("msgs_this_week", 0)
        msgs_prev = stats.get("msgs_prev_week", 0)
        error_week = stats.get("error_stats_week", {})
        error_prev = stats.get("error_stats_prev_week", {})

        top_error = max(error_week, key=error_week.get) if error_week else None
        improving = [k for k in error_week if error_prev.get(k, 0) > error_week.get(k, 0)]
        total_errors = sum(error_week.values()) if error_week else 0
        total_errors_prev = sum(error_prev.values()) if error_prev else 0

        data_summary = (
            f"Level: {level}\n"
            f"Streak: {streak} days in a row\n"
            f"Messages from student this week: {msgs_this} (previous week: {msgs_prev})\n"
            f"Total errors logged this week: {total_errors} (previous week: {total_errors_prev})\n"
            f"Top error category: {top_error if top_error else 'none'}\n"
            f"Improving categories: {improving if improving else 'none yet'}"
        )

        system = (
            "You are a warm, encouraging English learning coach writing a personal progress report.\n\n"
            "Write 4-6 sentences of flowing prose based on the student's stats below.\n"
            "Rules:\n"
            "- Be specific: reference actual numbers. Write the message count as \"X messages from you\" (e.g. \"125 messages from you\").\n"
            "- Highlight one genuine strength based on the numbers.\n"
            "- If there is a top error category, mention it as an area to focus on — "
            "name it naturally (e.g. 'grammar' or 'prepositions'), do NOT quote any examples.\n"
            "- If errors are improving compared to last week, acknowledge that progress.\n"
            "- End with one motivating encouragement.\n"
            "- Tone: honest and warm, like a good coach — not a cheerleader, not a report card.\n"
            "- NO headers, NO bullet points, NO markdown, NO ### symbols — pure flowing text only.\n"
            "- Write in English only.\n"
            "- Do NOT mention persona names (Greg, Mark, Jane, Mrs_smith etc).\n"
            "- Do NOT invent data not present in the stats (no vocabulary counts, no saved words)."
        )

        async def _deep_dive(client):
            response = await client.chat.completions.create(
                model="qwen/qwen3.6-27b",
                extra_body={"reasoning_format": "none"},",
                messages=[
                    {"role": "system", "content": system},
                    {"role": "user", "content": data_summary}
                ],
                temperature=0.7,
                max_tokens=250
            )
            return response.choices[0].message.content.strip()

        try:
            return await self._make_request(_deep_dive)
        except Exception as e:
            logger.error(f"❌ Ошибка deep dive stats: {e}")
            return "Couldn't generate your progress report right now. Try again later."

    async def generate_sunday_deep_dive(
        self,
        stats: Dict[str, Any],
        errors: list
    ) -> str:
        """
        Воскресный Deep Dive — личный разбор от Mrs. Smith.
        Педагогический анализ паттернов без цитат речи юзера.
        """
        from src.personas import get_persona_tutor_prompt as _get_tutor_prompt
        mrs_smith_prompt = _get_tutor_prompt("mrs_smith")

        msgs_this_week = stats.get("msgs_this_week", 0)
        streak = stats.get("streak_days", 0)

        # Реальные фразы пользователя — не придуманные примеры
        if errors:
            patterns_lines = []
            for e in errors[:4]:
                cat = e.get("category", "").strip()
                mistake = e.get("examples", [None])[0] if e.get("examples") else None
                corrected = e.get("corrected_text", "") or ""
                if not cat:
                    continue
                if mistake and corrected:
                    patterns_lines.append(f'- {cat}: user said "{mistake}" → correct: "{corrected}"')
                elif mistake:
                    patterns_lines.append(f'- {cat}: user said "{mistake}"')
                else:
                    patterns_lines.append(f'- {cat}')
            patterns_block = "Patterns from this week's conversations:\n" + "\n".join(patterns_lines) if patterns_lines else None
        else:
            patterns_block = None

        patterns_section = (
            patterns_block
            if patterns_block
            else "No recurring patterns this week — conversations flowed well."
        )

        system = (
            f"{mrs_smith_prompt}\n\n"
            "# TASK: SUNDAY DEEP DIVE\n"
            "Write a personal weekly note to your student. "
            "You have been quietly observing their English this week.\n\n"
            f"Facts: {msgs_this_week} messages from the student this week. Streak: {streak} days.\n"
            f"{patterns_section}\n\n"
            "# WRITING RULES\n"
            "— Warm and personal, like a letter, not a report card.\n"
            "— Mention the message count naturally in the opening as \"X messages from you\".\n"
            "— For each pattern: use the EXACT quote from the student's speech (provided above). "
            "Show what they said and how it should sound. "
            "Wrap the example block in <blockquote> tags like this:\n"
            "<blockquote>❌ Вы сказали: \"I work in Monday\"\n"
            "✅ Правильно: \"I work on Monday\"\n"
            "По-русски: с днями недели используется предлог on, не in.</blockquote>\n"
            "Put the blockquote on its own line. Never invent examples — only use the student's real words.\n"
            "— If no patterns: say something genuine about consistency or progress.\n"
            "— Close with a short personal note — something a real teacher would say.\n"
            "— NO markdown headers (no ###, no **bold**). Emojis are fine to break sections.\n"
            "— Max 300 words. Every sentence must earn its place.\n"
            "— Use <blockquote> tags exactly as shown. No other HTML tags."
        )

        async def _sunday(client):
            response = await client.chat.completions.create(
                model="qwen/qwen3.6-27b",
                extra_body={"reasoning_format": "none"},",
                messages=[
                    {"role": "system", "content": system},
                    {"role": "user", "content": "[write the deep dive]"}
                ],
                temperature=0.8,
                max_tokens=500
            )
            raw = response.choices[0].message.content.strip()
            # Страховка: убираем markdown если LLM всё равно добавил
            raw = re.sub(r'^#{1,3}\s*', '', raw, flags=re.MULTILINE)
            raw = re.sub(r'\*\*(.+?)\*\*', r'<b>\1</b>', raw)
            raw = re.sub(r'\*(.+?)\*', r'<i>\1</i>', raw)
            return raw.strip()

        try:
            return await self._make_request(_sunday)
        except Exception as e:
            logger.error(f"❌ Ошибка Sunday Deep Dive: {e}")
            return "I wasn't able to compile your report this week — but I've been watching your progress, and it shows. We'll catch up properly next Sunday."

    # ─── Re-engagement notification ────────────────────────────────────────

    async def generate_re_engagement_notification(
        self,
        persona_key: str,
        stats: Dict[str, Any],
        attempt: int = 0,
    ) -> str:
        from src.personas import get_persona_prompt
        persona_prompt = get_persona_prompt(persona_key)

        topic_pools = {
            "greg": [
                "You just finished a long shift and grabbed food on the way home.",
                "You're watching a Celtics game and it's going badly.",
                "You tried a new recipe from Mark and it actually worked.",
                "You had a weird case today you can't stop thinking about.",
                "You're procrastinating on studying and your phone is right there.",
            ],
            "mark": [
                "You just closed the kitchen after a rough service.",
                "You got a new seasonal ingredient and you're already planning something.",
                "You had a customer tonight who actually knew what they were eating.",
                "You've been thinking about a dish you want to put on the menu.",
                "Summer called earlier and it got you in a good mood.",
            ],
            "junior": [
                "Leo just said something hilarious at dinner.",
                "You've been debugging the same thing for two hours and need a break.",
                "Pixel knocked your coffee off the desk and you're not even mad.",
                "You just read something about AI that genuinely surprised you.",
                "Jane made you step away from the computer and you actually feel better.",
            ],
            "mrs_smith": [
                "You just finished grading and made yourself a cup of tea.",
                "You walked home through the park and it was unusually quiet.",
                "One of your students said something today that stuck with you.",
                "You've been reading something good and wanted someone to talk to about it.",
                "Your garden has something new coming up and you noticed it this morning.",
            ],
            "summer": [
                "You just landed somewhere new and the light here is incredible.",
                "You found a tiny café nobody knows about and you're sitting in it right now.",
                "You had a conversation with a stranger today that changed your afternoon.",
                "You're packing for the next trip and can't decide what to leave behind.",
                "You called Mark earlier and now you're missing home a little.",
            ],
            "jane": [
                "Both boys are finally asleep and you have five minutes to yourself.",
                "You saw something at the grocery store that made you think of a campaign idea.",
                "Junior said something ridiculous today and you're still laughing about it.",
                "You've been half-planning something and wanted to think it through out loud.",
                "You made a really good coffee this morning and the house was quiet for once.",
            ],
        }

        topics = topic_pools.get(persona_key, topic_pools["greg"])
        topic = random.choice(topics)

        user = stats.get("user", {})
        streak = stats.get("user", {}).get("streak_days", 0)
        msgs_this_week = stats.get("msgs_this_week", 0)

        stat_hints = []
        if streak > 0:
            stat_hints.append(f"they have a {streak} day streak going")
        if msgs_this_week > 0:
            stat_hints.append(f"they sent {msgs_this_week} messages this week")

        stat_line = ""
        if stat_hints:
            chosen_stat = random.choice(stat_hints)
            stat_line = (
                f"\nOptionally, if it fits naturally, you can mention that {chosen_stat}. "
                f"Only use it if it flows — don't force it."
            )

        if attempt == 0:
            tone = (
                "Write a short natural message checking in — 1 to 3 sentences. "
                "Light, casual. You just thought of them. No pressure. "
                "Speak as yourself in this moment. Don't be a coach or tutor. "
                "Don't use emojis. Don't sound like a notification. "
                "Just a real person reaching out."
            )
        elif attempt == 1:
            tone = (
                "Write a short message — 1 to 3 sentences. "
                "You noticed they haven't been around in a few days. "
                "A little more personal this time — like you actually miss talking. "
                "Still casual, still you. No guilt-tripping. Just genuine."
            )
        else:
            tone = (
                "Write a short farewell message — 1 to 3 sentences. "
                "It's been a while. You're not going to keep bothering them. "
                "Say goodbye in your own way — with character, not bitterness. "
                "A little warmth, a little humor, a clean close. "
                "Leave the door open without begging. This is the last message."
            )

        system = (
            f"{persona_prompt}\n\n"
            f"# SITUATION\n"
            f"{topic}\n\n"
            f"{tone}"
            f"{stat_line}"
        )

        async def _notify(client):
            response = await client.chat.completions.create(
                model="qwen/qwen3.6-27b",
                extra_body={"reasoning_format": "none"},",
                messages=[
                    {"role": "system", "content": system},
                    {"role": "user", "content": "[write the message]"}
                ],
                temperature=0.9,
                max_tokens=120
            )
            return response.choices[0].message.content.strip()

        try:
            return await self._make_request(_notify)
        except Exception as e:
            logger.error(f"❌ Ошибка генерации уведомления: {e}")
            return "Hey, it's been a while. Come back and let's talk!"

    # ─── Перевод ───────────────────────────────────────────────────────────

    async def translate_text(self, text: str, recast_phrases: Optional[List[str]] = None) -> str:
        # Строим инструкцию по bold
        if recast_phrases:
            phrases_str = ", ".join(f'"{p}"' for p in recast_phrases)
            bold_instruction = (
                f"4. These phrases were corrected for the user (recasting): {phrases_str}. "
                f"Find their translation equivalents and wrap them in **double asterisks**. "
                f"Do NOT bold anything else."
            )
        else:
            bold_instruction = "4. Do NOT add **asterisks** or any bold formatting to the translation."

        async def _translate(client):
            response = await client.chat.completions.create(
                model="qwen/qwen3.6-27b",
                extra_body={"reasoning_format": "none"},",
                messages=[
                    {
                        "role": "system",
                        "content": (
                            "You are a translator. Translate the given English text to Russian.\n\n"
                            "Rules:\n"
                            "1. Translate idioms and expressions by meaning, not literally. "
                            "Example: \'He\'s my rock\' → \'Он моя опора\', not \'Он моя скала\'. "
                            "Example: \'game-changer\' → \'это что-то особенное\'.\n"
                            "2. Preserve the casual conversational tone of the original.\n"
                            "3. Return only the translation, no comments or explanations.\n"
                            f"{bold_instruction}"
                        )
                    },
                    {"role": "user", "content": text}
                ],
                temperature=0.1,
                max_tokens=400
            )
            return response.choices[0].message.content

        try:
            return await self._make_request(_translate)
        except Exception as e:
            logger.error(f"❌ Ошибка перевода: {e}")
            return "Translation unavailable."

    # ─── TTS ───────────────────────────────────────────────────────────────

    async def text_to_speech(self, text: str, voice: Optional[str] = None) -> Optional[bytes]:
        if voice is None:
            voice = settings.TTS_VOICE

        async def _tts(client):
            response = await client.audio.speech.create(
                model="canopylabs/orpheus-v1-english",
                voice=voice,
                input=text,
                response_format="wav"
            )
            if hasattr(response, 'content'):
                return response.content
            elif hasattr(response, 'read'):
                return await response.read()
            else:
                return bytes(response)

        try:
            return await self._make_request(_tts)
        except Exception as e:
            logger.error(f"❌ Ошибка TTS: {e}")
            return None

    # ─── Tutor Mode: параллельная обработка ───────────────────────────────

    async def generate_tutor_response(
        self,
        text: str,
        user_level: str,
        history: Optional[List[Dict[str, str]]] = None,
        summary: Optional[str] = None,
        topics: Optional[str] = None,
        practice_error: Optional[Dict[str, Any]] = None,
        message_count: int = 0,
    ) -> str:
        tutor_prompt = get_persona_tutor_prompt("mrs_smith")

        memory_block = ""
        if summary:
            memory_block = (
                f"\n\n# WHAT YOU KNOW ABOUT THIS STUDENT\n"
                f"IMPORTANT: Everything below describes YOUR STUDENT — not you.\n"
                f"These are facts about the person you are teaching, NOT about Mrs. Smith.\n"
                f"{summary}"
            )

        topics_block = ""
        if topics and topics.strip():
            topics_block = (
                f"\n\n# TOPICS THIS STUDENT ENJOYS TALKING ABOUT\n"
                f"These have come up repeatedly: {topics}\n"
                f"Weave them in naturally when conversation allows."
            )

        practice_block = ""
        if practice_error and practice_error.get("corrected_text"):
            # Каждые ~3 сообщения — активный режим (вопрос-ловушка)
            # В остальное время — пассивный (моделируем форму)
            use_active = (message_count > 0 and message_count % 3 == 0)
            block_template = MISTAKES_PRACTICE_ACTIVE if use_active else MISTAKES_PRACTICE_PASSIVE
            practice_block = "\n\n" + block_template.format(
                category=practice_error.get("category", "grammar"),
                mistake=practice_error.get("mistake_text", ""),
                corrected=practice_error["corrected_text"],
            )

        system_prompt = (
            f"{tutor_prompt}{memory_block}{topics_block}{practice_block}\n\n"
            f"# STUDENT LEVEL\n{user_level.upper()}"
        )

        messages = [{"role": "system", "content": system_prompt}]
        if history:
            messages.extend(history)
        messages.append({"role": "user", "content": text})

        async def _chat(client):
            response = await client.chat.completions.create(
                model="qwen/qwen3.6-27b",
                extra_body={"reasoning_format": "none"},",
                messages=messages,
                temperature=0.75,
                max_tokens=300
            )
            return response.choices[0].message.content

        try:
            return await self._make_request(_chat)
        except Exception as e:
            logger.error(f"❌ Ошибка generate_tutor_response: {e}")
            return "Tell me more — I'm listening."

    async def process_user_message(
        self,
        telegram_id: int,
        user_text: str,
        user_level: str,
        history: Optional[List[Dict[str, str]]] = None,
        summary: Optional[str] = None,
        topics: Optional[str] = None,
        practice_error: Optional[Dict[str, Any]] = None,
        message_count: int = 0,
    ) -> Tuple[str, Dict[str, Any]]:
        """
        Tutor Mode: запускает два LLM параллельно.
        - correct_text → карточка ошибки (❌ / ✅ / 💡)
        - generate_tutor_response → ответ Mrs. Smith по смыслу
        Юзер получает оба баббла почти одновременно.
        """
        try:
            correction_task = self.correct_text(user_text, user_level)
            response_task = self.generate_tutor_response(
                user_text, user_level,
                history=history,
                summary=summary,
                topics=topics,
                practice_error=practice_error,
                message_count=message_count,
            )

            correction_result, chat_response = await asyncio.gather(
                correction_task, response_task
            )

            analysis_data = correction_result.copy()
            analysis_data['chat_response'] = chat_response

            return chat_response, analysis_data

        except Exception as e:
            logger.error(f"Error processing tutor message: {e}")
            return "Sorry, I encountered an error. Please try again.", {}

    # ─── Реакция Mrs. Smith на смену уровня ───────────────────────────────

    async def generate_level_change_reaction(
        self,
        old_level: str,
        new_level: str,
    ) -> str:
        """
        Живая TTS-реакция Mrs. Smith на повышение уровня.
        ~2 предложения, тёплая, личная, без «Great job!».
        Вызывается только при повышении (beginner→intermediate и т.д.).
        """
        direction = "up"  # этот метод вызывается только при повышении
        system = (
            "You are Mrs. Smith — a warm, observant English teacher with 20+ years of experience.\n"
            "A student just moved up a level in their English practice app.\n\n"
            "Write a SHORT personal reaction — 2 sentences maximum.\n"
            "Rules:\n"
            "- Be genuine and warm, not a cheerleader. No 'Great job!' or 'Wonderful!'.\n"
            "- Reference the specific levels naturally (e.g. 'moving to Intermediate').\n"
            "- End with a simple forward-looking thought — what this level means for them.\n"
            "- Speak as if you've been watching their progress. You noticed.\n"
            "- Never use ellipses, em-dashes as pauses, or trailing fragments.\n"
            "- Max 40 words total."
        )

        user_msg = (
            f"The student just moved from {old_level} to {new_level} level. "
            f"Write your reaction."
        )

        async def _react(client):
            response = await client.chat.completions.create(
                model="qwen/qwen3.6-27b",
                extra_body={"reasoning_format": "none"},",
                messages=[
                    {"role": "system", "content": system},
                    {"role": "user",   "content": user_msg},
                ],
                temperature=0.8,
                max_tokens=80,
            )
            return response.choices[0].message.content.strip()

        try:
            return await self._make_request(_react)
        except Exception as e:
            logger.error(f"❌ Ошибка level change reaction: {e}")
            return f"Moving to {new_level.capitalize()} — that's real progress. Let's see what you can do with it."

    # ─── PenFriend: мультибабл (Блок 2) ───────────────────────────────────

    async def generate_penfriend_multibubble(
        self,
        text: str,
        persona_key: str,
        history: Optional[List[Dict[str, str]]] = None,
        summary: Optional[str] = None,
        recasting_enabled: bool = False,
        practice_error: Optional[Dict[str, Any]] = None,
    ) -> List[str]:
        """
        PenFriend ответ для Greg, Jane, Summer, Junior — несколько коротких сообщений.
        Возвращает список строк (1–3 сообщения).
        Mrs. Smith и Mark возвращают список из одного элемента (обычный ответ).
        """
        # Mrs. Smith и Mark — одно сообщение, без мультибабла
        if persona_key in ("mrs_smith", "mark"):
            single = await self.generate_penfriend_response(
                text, persona_key,
                history=history,
                summary=summary,
                recasting_enabled=recasting_enabled,
                practice_error=practice_error,
            )
            return [single]

        persona_prompt = get_persona_prompt(persona_key, session_count=10)

        system_prompt = (
            f"{persona_prompt}"
            "\n\n# IMPORTANT: IGNORE PREVIOUS PERSONA"
            "\nPrevious assistant messages in this conversation may be from a DIFFERENT character."
            f"\nYou are {persona_key.upper()}. Do NOT adopt any personality, role, or identity from those messages."
            "\nOnly use the factual topics discussed — not who said them or how they spoke."
            "\n\n# PENFRIEND MULTIBUBBLE MODE\n"
            "You are texting — like a real person, not writing an essay.\n"
            "Real people split their thoughts into separate short messages.\n\n"
            "# YOUR TASK\n"
            "Reply as 2–3 separate text messages. Choose ONE of these styles:\n\n"
            "Style A — Split thought:\n"
            "  Message 1: start a thought\n"
            "  Message 2: finish or expand it\n"
            "  Message 3 (optional): follow-up question or reaction\n\n"
            "Style B — Emotion + content:\n"
            "  Message 1: short emotional reaction (e.g. 'Wait seriously??')\n"
            "  Message 2: your actual response\n\n"
            "# RULES\n"
            "- Each message: 1–3 sentences max\n"
            "- Natural casual English — contractions, shorthand fine\n"
            "- NO greetings, NO sign-offs, NO 'Hey!' at the start\n"
            "- Translate button appears only under the last message — write accordingly\n"
        )

        if summary:
            system_prompt += (
                f"\n\n# WHAT YOU KNOW ABOUT THIS PERSON\n{summary}\n"
                "Use this naturally, never dump it all at once."
            )

        if recasting_enabled:
            system_prompt += f"\n\n{RECASTING_BLOCK}"

        if practice_error and practice_error.get("corrected_text"):
            system_prompt += "\n\n" + MISTAKES_PRACTICE_PASSIVE.format(
                category=practice_error.get("category", "grammar"),
                mistake=practice_error.get("mistake_text", ""),
                corrected=practice_error["corrected_text"],
            )

        system_prompt += (
            '\n\n# OUTPUT FORMAT (json_object mode)\n'
            'Return ONLY a JSON object with these fields:\n'
            '{ "has_error": true/false, "correct_word": "corrected phrase or empty string", "messages": ["msg1", "msg2"] }\n'
            'Rules:\n'
            '- has_error: true if user made ANY grammar/spelling/vocabulary mistake\n'
            '- correct_word: the corrected word/phrase that naturally appears in your response (empty string if no error)\n'
            '- messages: 2-3 plain text strings, NO asterisks, NO bold\n'
            '- The correct_word MUST appear naturally in one of your messages\n'
            'Example: user wrote "I goed to store"\n'
            '{"has_error": true, "correct_word": "went to the store", "messages": ["Oh nice, you went to the store!", "What did you get?"]}'
        )

        messages = [{"role": "system", "content": system_prompt}]
        if history:
            messages.extend(history)
        messages.append({"role": "user", "content": text})

        async def _multibubble(client):
            response = await client.chat.completions.create(
                model="qwen/qwen3.6-27b",
                extra_body={"reasoning_format": "none"},",
                messages=messages,
                temperature=0.85,
                max_tokens=300,
                response_format={"type": "json_object"},
            )
            return response.choices[0].message.content

        raw = ""
        try:
            raw = await self._make_request(_multibubble)
            logger.info(f"Multibubble raw: {raw!r}")
            parsed = json.loads(raw)

            msgs = parsed.get("messages", [])
            if isinstance(msgs, str):
                msgs = [msgs]
            msgs = [m.strip() for m in msgs if m and m.strip()]
            if not msgs:
                return ["Ha, interesting. Tell me more."]

            # Если есть ошибка — прикрепляем correct_word маркером
            # message.py разберёт его, выделит bold и передаст переводчику
            has_error = parsed.get("has_error", False)
            correct_word = parsed.get("correct_word", "").strip() if has_error else ""
            if correct_word:
                msgs[0] = f"__RECAST__{correct_word}__RECAST__{msgs[0]}"

            return msgs
        except Exception as e:
            logger.error(f"❌ Ошибка PenFriend multibubble: {e}, raw={raw!r}")
            return ["Ha, interesting. Tell me more."]

    # ─── Автооценка уровня (Блок 2) ───────────────────────────────────────

    async def assess_user_level(
        self,
        messages: List[Dict[str, str]],
        current_level: str,
    ) -> Dict[str, str]:
        """
        Анализирует последние 10 сообщений пользователя и оценивает уровень.
        Запускается каждые 10 сообщений в handle_flow_message и handle_tutor_message.

        Возвращает:
        {
            "assessed_level": "intermediate",  # beginner/elementary/intermediate/advanced
            "confidence": "high"               # high/low
        }
        Если confidence != "high" или assessed_level == current_level → ничего не делать.
        Если assessed_level выше current_level и confidence == "high" → предложить повышение.
        """
        # Берём только реплики пользователя
        user_msgs = [
            m["content"] for m in messages
            if m.get("role") == "user" and m.get("content", "").strip()
        ]
        if not user_msgs:
            return {"assessed_level": current_level, "confidence": "low"}

        sample = "\n".join(f"- {m}" for m in user_msgs[-10:])

        system = (
            "You assess English proficiency from a sample of spoken/written messages.\n\n"
            "Levels: beginner, elementary, intermediate, advanced\n\n"
            "Criteria:\n"
            "- beginner: very simple sentences, many basic errors, limited vocabulary\n"
            "- elementary: simple sentences with common errors, basic vocabulary\n"
            "- intermediate: mostly correct sentences, some grammar issues, decent vocabulary\n"
            "- advanced: complex sentences, rare errors, wide vocabulary, natural flow\n\n"
            "confidence = 'high' only if the sample clearly and consistently matches a level.\n"
            "confidence = 'low' if the sample is too short, mixed, or ambiguous.\n\n"
            "Return ONLY valid JSON, no markdown:\n"
            '{"assessed_level": "...", "confidence": "high|low"}'
        )

        async def _assess(client):
            response = await client.chat.completions.create(
                model="qwen/qwen3.6-27b",
                extra_body={"reasoning_format": "none"},",
                messages=[
                    {"role": "system", "content": system},
                    {"role": "user",   "content": f"Current declared level: {current_level}\n\nMessages:\n{sample}"},
                ],
                temperature=0.0,
                max_tokens=50,
                response_format={"type": "json_object"},
            )
            return response.choices[0].message.content

        try:
            raw = await self._make_request(_assess)
            parsed = json.loads(raw)
            return {
                "assessed_level": parsed.get("assessed_level", current_level),
                "confidence": parsed.get("confidence", "low"),
            }
        except Exception as e:
            logger.error(f"❌ Ошибка автооценки уровня: {e}")
            return {"assessed_level": current_level, "confidence": "low"}


    async def generate_session_analysis(self, messages: list) -> str:
        """
        Анализирует сессию и возвращает текстовый разбор:
        темы разговора, ошибки пользователя, прогресс.
        Используется в Session Summary PDF.
        """
        user_messages = [m["content"] for m in messages if m.get("role") == "user"]
        if not user_messages:
            return "No user messages to analyze."

        sample = "\n".join(f"- {m}" for m in user_messages[-20:])

        system = (
            "You are an English language coach analyzing a student's conversation session.\n\n"
            "Write a concise Session Analysis in English. Structure:\n\n"
            "TOPICS DISCUSSED\n"
            "List the main topics covered in the conversation.\n\n"
            "LANGUAGE OBSERVATIONS\n"
            "Note patterns in the student's English: strengths, recurring errors, vocabulary range.\n\n"
            "PROGRESS NOTES\n"
            "One or two sentences on what went well and what to focus on next.\n\n"
            "Keep it factual, warm, and under 200 words total. No bullet points — use plain prose."
        )

        async def _analyze(client):
            response = await client.chat.completions.create(
                model="qwen/qwen3.6-27b",
                extra_body={"reasoning_format": "none"},",
                messages=[
                    {"role": "system", "content": system},
                    {"role": "user",   "content": f"Student messages from this session:\n{sample}"},
                ],
                temperature=0.3,
                max_tokens=400,
            )
            return response.choices[0].message.content.strip()

        try:
            return await self._make_request(_analyze)
        except Exception as e:
            logger.error(f"Error in generate_session_analysis: {e}")
            return "Analysis unavailable."

    async def suggest_synonym(self, word: str) -> str:
        """Предлагает один синоним для слова. Используется в Synonym Streak."""
        async def _syn(client):
            response = await client.chat.completions.create(
                model="qwen/qwen3.6-27b",
                messages=[
                    {"role": "system", "content": (
                        "You suggest a single English synonym for the given word.\n"
                        "Rules:\n"
                        "- Return ONLY the synonym word or short phrase (1-2 words max)\n"
                        "- Choose a natural, common alternative suitable for spoken English\n"
                        "- If no good synonym exists, return: NONE\n"
                        "- No explanations, no punctuation"
                    )},
                    {"role": "user", "content": word},
                ],
                temperature=0.3,
                max_tokens=10,
            )
            return response.choices[0].message.content.strip()

        try:
            result = await self._make_request(_syn)
            if result and result != "NONE" and len(result) < 30:
                return result
            return ""
        except Exception as e:
            logger.error(f"Error in suggest_synonym: {e}")
            return ""

    async def suggest_english_name(self, name: str) -> str:
        """
        Предлагает английский эквивалент имени пользователя.
        Например: Илья → Elijah, Пётр → Peter, Иван → John.
        Возвращает пустую строку если английский эквивалент не найден или имя уже английское.
        """
        system = (
            "You are a helpful assistant that knows name equivalents across languages.\n"
            "The user will give you a name. Your task:\n"
            "1. If the name has a well-known English equivalent (e.g. Илья→Elijah, Пётр→Peter, Иван→John, Мария→Mary), return ONLY that English name — nothing else.\n"
            "2. If the name is already English or has no common English equivalent, return exactly: NONE\n"
            "Rules:\n"
            "- Return ONLY the English name or NONE. No explanations, no punctuation.\n"
            "- Use the most common/classic English equivalent, not a transliteration.\n"
            "- Examples: Илья→Elijah, Александр→Alexander, Екатерина→Catherine, Михаил→Michael, Андрей→Andrew\n"
            "- If unsure, return NONE."
        )

        async def _suggest(client):
            response = await client.chat.completions.create(
                model="qwen/qwen3.6-27b",
                extra_body={"reasoning_format": "none"},",
                messages=[
                    {"role": "system", "content": system},
                    {"role": "user",   "content": name},
                ],
                temperature=0.0,
                max_tokens=20,
            )
            return response.choices[0].message.content.strip()

        try:
            result = await self._make_request(_suggest)
            if result and result != "NONE" and len(result) < 30:
                return result
            return ""
        except Exception as e:
            logger.error(f"Error in suggest_english_name: {e}")
            return ""


groq_client = GroqClient(settings.groq_api_keys_list)
