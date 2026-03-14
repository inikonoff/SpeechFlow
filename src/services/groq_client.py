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

RECASTING_BLOCK = """
# RECASTING INSTRUCTION
If the user makes a grammatical error, you MUST use the corrected version of their phrase naturally in your own response.
Rules:
1. Wrap ONLY the corrected words in **bold markdown** (**like this**).
2. Choose ONLY ONE error per message — the most important one. Ignore the rest.
3. NEVER say "you made a mistake", "that's wrong", or anything that sounds like correction.
4. Weave it in so naturally that it feels like your own speech, not a lesson.
5. If there are no errors — respond normally, no bold needed.
Example:
  User: "I looking forward to see you tomorrow!"
  You: "Oh I **am looking forward to seeing** you too! Do you want to grab coffee before we go?"
"""


class GroqClient:
    def __init__(self, api_keys: List[str]):
        self.clients = []
        self.current_index = 0

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

    def _get_next_client(self) -> Optional[AsyncOpenAI]:
        if not self.clients:
            return None
        client = self.clients[self.current_index]
        self.current_index = (self.current_index + 1) % len(self.clients)
        return client

    async def _make_request(self, func, *args, **kwargs):
        if not self.clients:
            raise Exception("Нет доступных Groq клиентов")

        errors = []
        for attempt in range(len(self.clients) * 2):
            client = self._get_next_client()
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
                temperature=0.0
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
                model="meta-llama/llama-4-scout-17b-16e-instruct",
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
    ) -> str:
        persona_prompt = get_persona_prompt(persona_key, session_count=session_count)

        if summary:
            persona_prompt += (
                f"\n\n# WHAT YOU KNOW ABOUT THIS PERSON\n{summary}\n"
                f"Use this naturally, never dump it all at once."
            )

        messages = [{"role": "system", "content": persona_prompt}]
        if history:
            messages.extend(history)
        messages.append({"role": "user", "content": text})

        async def _flow(client):
            response = await client.chat.completions.create(
                model="meta-llama/llama-4-scout-17b-16e-instruct",
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
    ) -> str:
        persona_prompt = get_persona_prompt(persona_key, session_count=10)

        system_prompt = (
            f"{persona_prompt}\n\n"
            "# PENFRIEND MODE\n"
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

        messages = [{"role": "system", "content": system_prompt}]
        if history:
            messages.extend(history)
        messages.append({"role": "user", "content": text})

        async def _penfriend(client):
            response = await client.chat.completions.create(
                model="meta-llama/llama-4-scout-17b-16e-instruct",
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
                model="meta-llama/llama-4-scout-17b-16e-instruct",
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
        summary: Optional[str] = None
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

        context_instruction = (
            "[greet the user for the first time — you've never spoken before]"
            if session_count == 0
            else "[you know this person — greet them naturally]"
        )

        system = (
            f"{persona_prompt}{memory_block}\n\n"
            f"# YOUR TASK\n"
            f"The person just chose to talk with you. Say hello in your own voice.\n"
            f"One or two sentences. Warm but natural, not over-the-top excited.\n"
            f"NEVER say 'finally', 'I missed you', 'it's so great to see you again'.\n"
            f"End with a simple opening question."
        )

        async def _greeting(client):
            response = await client.chat.completions.create(
                model="meta-llama/llama-4-scout-17b-16e-instruct",
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
                model="meta-llama/llama-4-scout-17b-16e-instruct",
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
                model="meta-llama/llama-4-scout-17b-16e-instruct",
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
                model="meta-llama/llama-4-scout-17b-16e-instruct",
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

        data_summary = (
            f"Level: {level}\n"
            f"Streak: {streak} days in a row\n"
            f"Messages this week: {msgs_this} (previous week: {msgs_prev})\n"
            f"Error categories this week: {dict(error_week)}\n"
            f"Improving categories (fewer errors than last week): {improving}\n"
            f"Top error category: {top_error}"
        )

        system = (
            "You are a warm, encouraging English learning coach writing a personal progress report.\n\n"
            "Write 4-6 sentences of flowing prose based on the student's stats below.\n"
            "Rules:\n"
            "- Be specific: reference actual numbers from the data.\n"
            "- Highlight one genuine strength.\n"
            "- Name the main area to work on (use the top error category if available).\n"
            "- End with one concrete, actionable encouragement.\n"
            "- Tone: honest and warm, like a good coach — not a cheerleader, not a report card.\n"
            "- NO headers, NO bullet points, NO markdown, NO ### symbols — pure flowing text only.\n"
            "- Write in English only.\n"
            "- Do NOT mention the persona names (Greg, Mark, Jane, Mrs_smith etc) — "
            "those are the student's conversation partners, not their name.\n"
            "- Do NOT invent data that is not in the stats (no vocabulary counts, no word savings)."
        )

        async def _deep_dive(client):
            response = await client.chat.completions.create(
                model="meta-llama/llama-4-scout-17b-16e-instruct",
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
        Воскресный Deep Dive — отчёт от Mrs. Smith с анализом ошибок за неделю.
        Отличается от stats deep dive тем, что оперирует конкретными примерами ошибок.
        """
        from src.personas import get_persona_tutor_prompt as _get_tutor_prompt
        mrs_smith_prompt = _get_tutor_prompt("mrs_smith")

        user = stats.get("user", {})
        msgs_this_week = stats.get("msgs_this_week", 0)

        errors_block_lines = []
        for e in errors[:3]:
            cat = e.get("category", "")
            examples = e.get("examples", [])
            example = examples[0] if examples else ""
            if example:
                errors_block_lines.append(
                    f"Pattern: {cat}\n"
                    f"Student said: \"{example}\""
                )

        errors_block = (
            "\n\n".join(errors_block_lines)
            if errors_block_lines
            else "No recurring errors this week — a genuinely strong week."
        )

        system = (
            f"{mrs_smith_prompt}\n\n"
            "# SUNDAY DEEP DIVE\n"
            "You are writing a weekly personal report directly to your student.\n"
            "You have been quietly reviewing their conversations this week.\n\n"
            f"Student activity this week: {msgs_this_week} messages sent.\n\n"
            f"Language patterns noticed:\n{errors_block}\n\n"
            "# YOUR INSTRUCTIONS\n"
            "1. Open warmly — acknowledge their effort with the specific message count.\n"
            "2. For each language pattern: show their example (❌), then the correct version (✅), "
            "then explain the rule in ONE friendly sentence in Russian.\n"
            "3. End with a short personal encouraging note.\n"
            "4. Use emojis naturally for readability.\n"
            "5. CRITICAL: Do NOT use ### or ## or any markdown headers. "
            "Separate sections with a blank line and an emoji if needed — nothing else.\n"
            "6. Do NOT sound like a school report card. Sound like a teacher who knows this student."
        )

        async def _sunday(client):
            response = await client.chat.completions.create(
                model="meta-llama/llama-4-scout-17b-16e-instruct",
                messages=[
                    {"role": "system", "content": system},
                    {"role": "user", "content": "[write the Sunday Deep Dive report]"}
                ],
                temperature=0.75,
                max_tokens=450
            )
            return response.choices[0].message.content.strip()

        try:
            return await self._make_request(_sunday)
        except Exception as e:
            logger.error(f"❌ Ошибка Sunday Deep Dive: {e}")
            return "I wasn't able to compile your report this week — but I've been watching your progress, and it shows. We'll catch up properly next Sunday."

    # ─── Re-engagement notification ────────────────────────────────────────

    async def generate_re_engagement_notification(
        self,
        persona_key: str,
        stats: Dict[str, Any]
    ) -> str:
        from src.personas import get_persona_prompt
        persona_prompt = get_persona_prompt(persona_key)

        user = stats.get("user", {})
        streak = user.get("streak_days", 0)

        system = (
            f"{persona_prompt}\n\n"
            f"# YOUR TASK\n"
            f"The user hasn't opened the app in a while. Send them a short, personal message "
            f"in your own voice — like a friend checking in, not a system notification.\n"
            f"They currently have a {streak} day streak. Mention it casually if it feels natural.\n"
            f"Keep it warm, brief (2-3 sentences max), and end with a natural invitation to come back and talk.\n"
            f"Don't be pushy. Don't say 'I missed you' if it feels forced."
        )

        async def _notify(client):
            response = await client.chat.completions.create(
                model="meta-llama/llama-4-scout-17b-16e-instruct",
                messages=[
                    {"role": "system", "content": system},
                    {"role": "user", "content": "[write the re-engagement message]"}
                ],
                temperature=0.85,
                max_tokens=150
            )
            return response.choices[0].message.content.strip()

        try:
            return await self._make_request(_notify)
        except Exception as e:
            logger.error(f"❌ Ошибка генерации уведомления: {e}")
            return "Hey, it's been a while. Come back and let's talk!"

    # ─── Перевод ───────────────────────────────────────────────────────────

    async def translate_text(self, text: str) -> str:
        async def _translate(client):
            response = await client.chat.completions.create(
                model="meta-llama/llama-4-scout-17b-16e-instruct",
                messages=[
                    {
                        "role": "system",
                        "content": (
                            "You are a translator. Translate the given English text to Russian. "
                            "Return only the translation, no comments or explanations."
                        )
                    },
                    {"role": "user", "content": text}
                ],
                temperature=0.0,
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

        system_prompt = (
            f"{tutor_prompt}{memory_block}{topics_block}\n\n"
            f"# STUDENT LEVEL\n{user_level.upper()}"
        )

        messages = [{"role": "system", "content": system_prompt}]
        if history:
            messages.extend(history)
        messages.append({"role": "user", "content": text})

        async def _chat(client):
            response = await client.chat.completions.create(
                model="meta-llama/llama-4-scout-17b-16e-instruct",
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


groq_client = GroqClient(settings.groq_api_keys_list)
