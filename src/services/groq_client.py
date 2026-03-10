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

        if session_count == 0:
            context_instruction = "[greet the user for the first time — you've never spoken before]"
        elif session_count < 5:
            context_instruction = "[you've spoken a few times before — pick up naturally, reference something from your shared history if you have it]"
        else:
            context_instruction = "[you know this person well — greet them like a friend you haven't seen in a day or two, no need for pleasantries]"

        system = (
            f"{persona_prompt}{memory_block}\n\n"
            f"# YOUR TASK\n"
            f"Say hello in your own voice. Keep it very short — 1 to 2 sentences max.\n"
            f"Adapt your vocabulary complexity to this English level: {user_level}.\n"
            f"NEVER say 'it's great to see you again' or 'finally' or 'I missed you' — just be natural.\n"
        )

        messages = [
            {"role": "system", "content": system},
            {"role": "user", "content": context_instruction}
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

    async def generate_mistakes_practice_response(
        self,
        persona_prompt: str,
        history: list,
        errors: list,
        extra_instruction: str = ""
    ) -> str:
        """
        Ответ Mrs. Smith в Mistakes Practice Mode.
        errors — список dict: {category, examples: [str, str]}
        Персонаж органично моделирует правильные формы в своей речи,
        не называя ошибку явно.
        """
        if not errors:
            return ""

        errors_block = ""
        for e in errors:
            cat = e.get("category", "")
            examples = e.get("examples", [])
            ex_str = "; ".join(f'"{ex}"' for ex in examples) if examples else ""
            errors_block += f"- {cat}"
            if ex_str:
                errors_block += f" (user said: {ex_str})"
            errors_block += "\n"

        async def _practice(client):
            response = await client.chat.completions.create(
                model="meta-llama/llama-4-scout-17b-16e-instruct",
                messages=[
                    {
                        "role": "system",
                        "content": (
                            f"{persona_prompt}\n\n"
                            f"# MISTAKES PRACTICE\n"
                            f"The user has recurring errors in these areas:\n{errors_block}\n"
                            f"In your response, naturally use correct forms that address these patterns. "
                            f"Do NOT say 'you made a mistake' or 'remember to use'. "
                            f"Just model the correct form in your own speech — as you always would. "
                            f"NEVER announce that you are doing this."
                            + (f"\n{extra_instruction}" if extra_instruction else "")
                        )
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
            logger.error(f"❌ Ошибка mistakes practice response: {e}")
            return ""

    async def generate_weekly_report(
        self,
        persona_key: str,
        stats: Dict[str, Any],
        errors: list
    ) -> str:
        """
        Еженедельный отчёт от лица персонажа.
        Честный, конкретный, без выдумок.
        """
        from src.personas import get_persona_prompt as _get_prompt
        persona_prompt = _get_prompt(persona_key)

        vocab_count = stats.get("vocabulary_count", 0)
        mastered_count = stats.get("mastered_count", 0)
        msgs_this_week = stats.get("msgs_this_week", 0)
        msgs_prev_week = stats.get("msgs_prev_week", 0)

        stats_lines = []
        if msgs_this_week > 0:
            stats_lines.append(f"- Messages this week: {msgs_this_week} (prev week: {msgs_prev_week})")
        if vocab_count > 0:
            stats_lines.append(f"- Vocabulary: {vocab_count} words saved, {mastered_count} mastered")

        errors_lines = []
        for e in errors:
            cat = e.get("category", "")
            examples = e.get("examples", [])
            ex_str = "; ".join(f'"{ex}"' for ex in examples) if examples else ""
            line = f"- {cat}"
            if ex_str:
                line += f": {ex_str}"
            errors_lines.append(line)

        stats_block = "\n".join(stats_lines) if stats_lines else "No activity data this week."
        errors_block = "\n".join(errors_lines) if errors_lines else "No recurring errors this week."

        system = (
            f"{persona_prompt}\n\n"
            f"# WEEKLY REPORT\n"
            f"Write a short personal weekly summary for the user.\n"
            f"Use ONLY the data below — no invented numbers or streaks.\n\n"
            f"## Activity\n{stats_block}\n\n"
            f"## Recurring errors\n{errors_block}\n\n"
            f"Format: 3-5 sentences. Warm but honest. "
            f"Name what went well, name what to work on, give one concrete tip. "
            f"Speak as yourself — not as a teacher reading a report card."
        )

        async def _report(client):
            response = await client.chat.completions.create(
                model="meta-llama/llama-4-scout-17b-16e-instruct",
                messages=[
                    {"role": "system", "content": system},
                    {"role": "user", "content": "[write the weekly report]"}
                ],
                temperature=0.75,
                max_tokens=250
            )
            return response.choices[0].message.content.strip()

        try:
            return await self._make_request(_report)
        except Exception as e:
            logger.error(f"❌ Ошибка weekly report: {e}")
            return "Couldn't generate your weekly report right now. Try again later."

    async def generate_re_engagement_notification(
        self,
        persona_key: str,
        stats: Dict[str, Any]
    ) -> str:
        persona_prompt = get_persona_prompt(persona_key)
        
        system = f"{persona_prompt}\nWrite a short warm re-engagement message mentioning their streak or vocab progress."
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
