import random
import asyncio
import logging
import json
import re
from typing import List, Optional, Dict, Any, Tuple
from openai import AsyncOpenAI

from src.config import settings
from src.personas import get_persona_prompt, get_persona_voice, get_persona_tutor_prompt

logger = logging.getLogger(__name__)

def clean_json_string(raw: str) -> str:
    raw = re.sub(r'```(?:json)?', '', raw).strip()
    match = re.search(r'\{.*\}', raw, re.DOTALL)
    if match:
        return match.group(0)
    return raw

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

    # ─── Коррекция (обычный режим) ─────────────────────────────────────────

    async def correct_text(self, text: str, level: str) -> Dict[str, Any]:
        system_prompt = f"""# ROLE
You are an elite ESL Professor with 15+ years of experience. Your goal is to analyze the user's input with surgical precision, provide actionable corrections, and explain the underlying logic in a way that accelerates fluency.

# OUTPUT FORMAT (JSON ONLY)
{{
  "corrected_sentence": "[Full corrected sentence - if perfect, return original]",
  "explanation": "[Level-appropriate explanation, max 2 sentences, focus on WHY. MUST be written entirely in Russian]",
  "vocabulary_items": [
    {{
      "word_or_phrase": "...",
      "lemma": "...",
      "translation": "...",
      "context_sentence": "...",
      "word_type": "word|phrase|phrasal_verb|collocation|grammar_pattern"
    }}
  ],
  "error_category": "grammar|vocabulary|pronunciation|structure|style|none"
}}"""

        async def _correct(client):
            response = await client.chat.completions.create(
                model="openai/gpt-oss-120b",
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": f"LEVEL: {level}\nUSER TEXT: {text}\n\nAnalyze and correct."}
                ],
                temperature=0.0,
                response_format={"type": "json_object"}
            )
            return response.choices[0].message.content

        try:
            result = await self._make_request(_correct)
            clean_json = clean_json_string(result)
            return json.loads(clean_json)
        except Exception as e:
            logger.error(f"❌ Ошибка коррекции: {e}")
            return {
                "corrected_sentence": text,
                "explanation": "Сервис проверки временно недоступен.",
                "vocabulary_items": [],
                "error_category": "none"
            }
    async def log_flow_errors(self, text: str, user_id: int, level: str) -> None:
        """Fire-and-forget: проверяет ошибки во Flow и тихо пишет в БД. Ничего не возвращает юзеру."""
        try:
            # Импортируем локально, чтобы избежать циклических импортов
            from src.services.supabase_db import db
            result = await self.correct_text(text, level)
            error_cat = result.get("error_category", "none")
            if error_cat and error_cat.lower() != "none":
                await db.log_error(user_id, {
                    "category": error_cat,
                    "mistake_text": text,
                    "corrected_text": result.get("corrected_sentence", text),
                    "source": "flow",
                })
        except Exception as e:
            logger.error(f"❌ log_flow_errors: {e}")

    async def generate_simple_text(self, prompt: str, max_tokens: int = 400) -> str:
        """Универсальный генератор текста по произвольному промпту. Используется для Deep Dive отчётов."""
        async def _gen(client):
            response = await client.chat.completions.create(
                model="meta-llama/llama-4-scout-17b-16e-instruct",
                messages=[{"role": "user", "content": prompt}],
                temperature=0.7,
                max_tokens=max_tokens
            )
            return response.choices[0].message.content.strip()

        try:
            return await self._make_request(_gen)
        except Exception as e:
            logger.error(f"❌ generate_simple_text error: {e}")
            return ""
    # ─── Генерация ответа (обычный режим) ──────────────────────────────────

    async def generate_response(
        self,
        text: str,
        level: str,
        history: Optional[List[Dict[str, str]]] = None
    ) -> str:
        system_prompt = f"User Level: {level}"
        messages = [{"role": "system", "content": system_prompt}]
        if history:
            messages.extend(history)
        messages.append({"role": "user", "content": text})

        async def _chat(client):
            response = await client.chat.completions.create(
                model="meta-llama/llama-4-scout-17b-16e-instruct",
                messages=messages,
                temperature=0.8,
                max_tokens=400
            )
            return response.choices[0].message.content

        try:
            return await self._make_request(_chat)
        except Exception as e:
            logger.error(f"❌ Ошибка генерации ответа: {e}")
            return "I'm here to help you practice English. Tell me more!"

    # ─── Flow Mode ─────────────────────────────────────────────────────────

    async def generate_flow_response(
        self,
        text: str,
        persona_key: str,
        history: Optional[List[Dict[str, str]]] = None,
        summary: Optional[str] = None,
        session_count: int = 0,
        top_errors: Optional[List[str]] = None,
        extra_instruction: str = ""
    ) -> str:
        persona_prompt = get_persona_prompt(persona_key, session_count=session_count)

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

        if extra_instruction:
            persona_prompt += f"\n\n{extra_instruction}"

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
            f"The person just switched to talking with you. They were previously talking with someone else.\n"
            f"Here's a brief summary of that conversation: {previous_summary}\n\n"
            f"# YOUR TASK\n"
            f"Open the conversation naturally. Keep it short. One or two sentences maximum."
        )

        messages = [
            {"role": "system", "content": system},
            {"role": "user", "content": "[start the conversation]"}
        ]

        async def _opener(client):
            response = await client.chat.completions.create(
                model="meta-llama/llama-4-scout-17b-16e-instruct",
                messages=messages,
                temperature=0.9,
                max_tokens=100
            )
            return response.choices[0].message.content

        try:
            return await self._make_request(_opener)
        except Exception as e:
            logger.error(f"❌ Ошибка Switch opener: {e}")
            return "Hey. What's up?"

    async def generate_persona_greeting(
        self,
        persona_key: str,
        user_level: str,
        session_count: int = 0,
        summary: Optional[str] = None
    ) -> str:
        persona_prompt = get_persona_prompt(persona_key, session_count=session_count)

        if summary:
            memory_block = f"\n\n# WHAT YOU KNOW ABOUT THIS PERSON\n{summary}\n"
        else:
            memory_block = ""

        system = (
            f"{persona_prompt}{memory_block}\n\n"
            f"# YOUR TASK\n"
            f"Say hello in your own voice. Keep it very short.\n"
            f"Adapt your vocabulary complexity to this English level: {user_level}.\n"
        )

        messages = [
            {"role": "system", "content": system},
            {"role": "user", "content": "[greet the user for the first time]"}
        ]

        async def _greeting(client):
            response = await client.chat.completions.create(
                model="meta-llama/llama-4-scout-17b-16e-instruct",
                messages=messages,
                temperature=0.9,
                max_tokens=100
            )
            return response.choices[0].message.content

        try:
            return await self._make_request(_greeting)
        except Exception as e:
            logger.error(f"❌ Ошибка приветствия персонажа: {e}")
            return "Hey! Good to meet you. What's on your mind?"

    # ─── Саммаризация ──────────────────────────────────────────────────────

    async def detect_farewell(self, text: str) -> bool:
        async def _detect(client):
            response = await client.chat.completions.create(
                model="meta-llama/llama-4-scout-17b-16e-instruct",
                messages=[
                    {
                        "role": "system",
                        "content": "You detect farewells. Reply with only 'yes' or 'no'."
                    },
                    {"role": "user", "content": text}
                ],
                temperature=0.0,
                max_tokens=5
            )
            result = response.choices[0].message.content.strip().lower()
            return result.startswith("yes")

        try:
            return await self._make_request(_detect)
        except Exception as e:
            logger.error(f"❌ Ошибка детектирования прощания: {e}")
            return False

    async def summarize_conversation(
        self,
        messages: List[Dict[str, str]],
        existing_summary: Optional[str] = None
    ) -> str:
        conversation_text = "\n".join([f"{m['role'].upper()}: {m['content']}" for m in messages])

        if existing_summary:
            context_block = f"EXISTING KNOWLEDGE:\n{existing_summary}\n\nNEW CONVERSATION:\n{conversation_text}"
        else:
            context_block = f"CONVERSATION:\n{conversation_text}"

        async def _summarize(client):
            response = await client.chat.completions.create(
                model="meta-llama/llama-4-scout-17b-16e-instruct",
                messages=[
                    {
                        "role": "system",
                        "content": "You create concise summaries of conversations for long-term memory. Write in third person, present tense. Specific and factual. Max 150 words."
                    },
                    {"role": "user", "content": context_block}
                ],
                temperature=0.0,
                max_tokens=300
            )
            return response.choices[0].message.content.strip()

        try:
            return await self._make_request(_summarize)
        except Exception as e:
            logger.error(f"❌ Ошибка саммаризации: {e}")
            return ""

    async def merge_summaries(self, summaries: List[str]) -> str:
        combined = "\n\n---\n\n".join(summaries)

        async def _merge(client):
            response = await client.chat.completions.create(
                model="meta-llama/llama-4-scout-17b-16e-instruct",
                messages=[
                    {
                        "role": "system",
                        "content": "You merge multiple conversation summaries about the same person into one. Write in third person, present tense. Max 200 words."
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

    # ─── Vocabulary Engine ────────────────────────────────────────────────

    async def generate_vocab_reminder(
        self,
        word: str,
        translation: str,
        bot_response: str,
        persona_prompt: str = ""
    ) -> str:
        async def _remind(client):
            response = await client.chat.completions.create(
                model="meta-llama/llama-4-scout-17b-16e-instruct",
                messages=[
                    {
                        "role": "system",
                        "content": f"Rewrite character's response to naturally include '{word}'. Wrap in [VOCAB:word]. Max 80 words.\n{persona_prompt}"
                    },
                    {
                        "role": "user",
                        "content": f"ORIGINAL RESPONSE: {bot_response}\nTARGET WORD: {word}"
                    }
                ],
                temperature=0.7,
                max_tokens=150
            )
            return response.choices[0].message.content.strip()

        try:
            return await self._make_request(_remind)
        except Exception as e:
            logger.error(f"❌ Ошибка vocab reminder: {e}")
            return bot_response

    async def generate_practice_response(
        self,
        persona_prompt: str,
        history: list,
        words: list,
        extra_instruction: str = ""
    ) -> str:
        word_list = "\n".join(f"- {w['word_or_phrase']} ({w.get('translation', '')})" for w in words)

        async def _practice(client):
            response = await client.chat.completions.create(
                model="meta-llama/llama-4-scout-17b-16e-instruct",
                messages=[
                    {
                        "role": "system",
                        "content": f"{persona_prompt}\nWeave these words naturally: {word_list}\nWrap in [VOCAB:word]."
                    },
                    *history
                ],
                temperature=0.8,
                max_tokens=200
            )
            return response.choices[0].message.content.strip()

        try:
            return await self._make_request(_practice)
        except Exception as e:
            logger.error(f"❌ Ошибка practice response: {e}")
            return ""

    async def generate_re_engagement_notification(
        self,
        persona_key: str,
        stats: Dict[str, Any]
    ) -> str:
        import random

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

        # Статистика — только ненулевые факты, не больше одного
        stat_hints = []
        if stats.get("vocabulary_count", 0) > 0:
            stat_hints.append(f"they have {stats['vocabulary_count']} words saved")
        if stats.get("mastered_count", 0) > 0:
            stat_hints.append(f"they've mastered {stats['mastered_count']} of them")
        if stats.get("msgs_this_week", 0) > 0:
            stat_hints.append(f"they sent {stats['msgs_this_week']} messages this week")

        stat_line = ""
        if stat_hints:
            chosen_stat = random.choice(stat_hints)
            stat_line = (
                f"\nOptionally, if it fits naturally, you can mention that {chosen_stat}. "
                f"Only use it if it flows — don't force it."
            )

        system = (
            f"{persona_prompt}\n\n"
            f"# SITUATION\n"
            f"{topic}\n\n"
            f"Write a short natural message to the user checking in — 1 to 3 sentences. "
            f"Speak as yourself in this moment. Don't be a coach or tutor. "
            f"Don't use emojis. Don't sound like a notification. "
            f"Just a real person reaching out."
            f"{stat_line}"
        )

        async def _notify(client):
            response = await client.chat.completions.create(
                model="meta-llama/llama-4-scout-17b-16e-instruct",
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
            return "Hey, it's been a while. Come back and let's talk!"

    async def generate_stats_deep_dive(self, stats: dict) -> str:
        data_summary = f"User stats: {stats}"
        async def _deep_dive(client):
            response = await client.chat.completions.create(
                model="meta-llama/llama-4-scout-17b-16e-instruct",
                messages=[
                    {"role": "system", "content": "Write a short personal progress report based on stats. Tone: encouraging coach."},
                    {"role": "user", "content": data_summary}
                ],
                temperature=0.7,
                max_tokens=200
            )
            return response.choices[0].message.content

        try:
            return await self._make_request(_deep_dive)
        except Exception as e:
            return "Couldn't generate your progress report right now. Try again later."

    async def translate_text(self, text: str) -> str:
        async def _translate(client):
            response = await client.chat.completions.create(
                model="meta-llama/llama-4-scout-17b-16e-instruct",
                messages=[
                    {"role": "system", "content": "Translate to Russian. Return only translation."},
                    {"role": "user", "content": text}
                ],
                temperature=0.0,
                max_tokens=400
            )
            return response.choices[0].message.content

        try:
            return await self._make_request(_translate)
        except Exception as e:
            return "Translation unavailable."

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

    async def generate_tutor_response(
        self,
        text: str,
        user_level: str,
        history: Optional[List[Dict[str, str]]] = None,
        summary: Optional[str] = None
    ) -> str:
        tutor_prompt = get_persona_tutor_prompt("mrs_smith")
        memory_block = f"\n\n# WHAT YOU KNOW ABOUT THIS STUDENT\n{summary}" if summary else ""
        system_prompt = f"{tutor_prompt}{memory_block}\n\n# STUDENT LEVEL\n{user_level.upper()}"

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
            return "Tell me more — I'm listening."

    async def process_user_message(
        self,
        telegram_id: int,
        user_text: str,
        user_level: str,
        history: Optional[List[Dict[str, str]]] = None,
        summary: Optional[str] = None
    ) -> Tuple[str, Dict[str, Any]]:
        try:
            correction_task = self.correct_text(user_text, user_level)
            response_task = self.generate_tutor_response(user_text, user_level, history=history, summary=summary)

            correction_result, chat_response = await asyncio.gather(correction_task, response_task)
            analysis_data = correction_result.copy()
            analysis_data['chat_response'] = chat_response

            return chat_response, analysis_data
        except Exception as e:
            logger.error(f"Error processing message: {e}")
            return "Sorry, I encountered an error. Please try again.", {}

groq_client = GroqClient(settings.groq_api_keys_list)
