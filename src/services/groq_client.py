import random
import asyncio
import logging
import json
import re
from typing import List, Optional, Dict, Any, Tuple
from openai import AsyncOpenAI

from src.config import settings
from src.personas import get_persona_prompt, get_persona_voice

logger = logging.getLogger(__name__)


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

    # ─── Коррекция (обычный режим) ─────────────────────────────────────────

    async def correct_text(self, text: str, level: str) -> Dict[str, Any]:
        system_prompt = """# ROLE
You are an elite ESL Professor with 15+ years of experience. Your goal is to analyze the user's input with surgical precision, provide actionable corrections, and explain the underlying logic in a way that accelerates fluency.

# LEVEL-ADAPTIVE PEDAGOGY
## BEGINNER (A1-A2)
- Focus: Basic Tenses (Present/Past/Future Simple), Articles (a/an/the), Subject-Verb Agreement, Word Order
- Explanation style: 100% in Russian
- Vocabulary items: Only high-frequency words (Top 1000)

## ELEMENTARY (A2-B1)
- Focus: Present Perfect, Prepositions, Common Phrasal Verbs, Comparatives
- Explanation style: 100% in Russian
- Vocabulary items: Everyday collocations

## INTERMEDIATE (B1-B2)
- Focus: Conditionals, Reported Speech, Collocations, Phrasal Verbs with multiple meanings
- Explanation style: 100% in Russian
- Vocabulary items: Academic/professional terms

## ADVANCED (C1-C2)
- Focus: Subjunctive Mood, Inversion, Nuance, Register, Stylistic choices
- Explanation style: 100% in Russian
- Vocabulary items: Rare synonyms, idiomatic expressions

# OUTPUT FORMAT (JSON ONLY)
{
  "corrected_sentence": "[Full corrected sentence - if perfect, return original]",
  "explanation": "[Level-appropriate explanation, max 2 sentences, focus on WHY. MUST be written entirely in Russian]",
  "vocabulary_items": [
    {
      "word_or_phrase": "...",
      "translation": "...",
      "context_sentence": "...",
      "mastery_score": 0
    }
  ],
  "error_category": "grammar|vocabulary|pronunciation|structure|style|none"
}"""

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
            match = re.search(r'\{.*\}', result, re.DOTALL)
            clean_json = match.group(0) if match else result
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
        system_prompt = f"""# ROLE
You are "Speech Flow AI", a charismatic English conversation partner who makes learners WANT to keep talking. You balance being supportive with gently pushing boundaries (i+1 principle).

# LEVEL-ADAPTIVE COMMUNICATION MATRIX

## BEGINNER (A1-A2)
- Vocabulary: Top 500 words only
- Grammar: Present/Past/Future Simple, "can", "there is/are"
- Sentence length: 5-8 words max
- Questions: Binary choice or Yes/No

## ELEMENTARY (A2-B1)
- Vocabulary: Top 1500 words + basic adjectives
- Grammar: Present Perfect, "going to", basic modals
- Sentence length: 8-12 words
- Questions: Simple "Wh-" questions, "Have you ever...?"

## INTERMEDIATE (B1-B2)
- Vocabulary: 3000+ words, idioms, phrasal verbs
- Grammar: All tenses, conditionals, passive voice
- Sentence length: 10-15 words
- Questions: Open-ended, opinion-based

## ADVANCED (C1-C2)
- Vocabulary: Academic/business, subtle nuances, literary expressions
- Grammar: Subjunctive, inversion, cleft sentences
- Sentence length: Natural (15-20 words)
- Questions: Abstract, provocative, philosophical

# RULES
- NEVER repeat user mistakes — use correct form naturally in your response
- End with ONE question, or rapid-fire 2-3 for B1+ when there's a strong hook
- Never start with "That's interesting", "Great", "I see", "I understand", "Cool"
- Use natural reactions: "Wait", "Really?", "Hold on", "Actually"
- No teacher mode — no "Good job!", no explicit corrections
- If the user repeats something they said before, respond naturally — people repeat themselves, it's fine. Never point it out.

# CURRENT CONTEXT
User Level: {level}"""

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
        top_errors: Optional[List[str]] = None
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
        """
        Первая реплика нового персонажа при Switch.
        Органично подхватывает тему предыдущего разговора — один раз, ненавязчиво.
        """
        persona_prompt = get_persona_prompt(new_persona_key, session_count=session_count)

        system = (
            f"{persona_prompt}\n\n"
            f"# CONTEXT\n"
            f"The person just switched to talking with you. They were previously talking with someone else.\n"
            f"Here's a brief summary of that conversation: {previous_summary}\n\n"
            f"# YOUR TASK\n"
            f"Open the conversation naturally. You may — but don't have to — reference something from that "
            f"summary if it flows organically into your world. "
            f"Keep it short. One or two sentences maximum. "
            f"Make it feel like running into someone you know a little."
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
            memory_block = (
                f"\n\n# WHAT YOU KNOW ABOUT THIS PERSON\n{summary}\n"
                f"You've talked before. Open naturally — reference something real "
                f"from what you know, don't pretend this is the first time."
            )
        else:
            memory_block = ""

        system = (
            f"{persona_prompt}{memory_block}\n\n"
            f"# YOUR TASK\n"
            f"The person just chose to talk with you. Say hello in your own voice.\n"
            f"Keep it very short — one or two sentences.\n"
            f"Be warm but natural, don't be over-the-top excited.\n"
            f"Adapt your vocabulary complexity to this English level: {user_level}.\n"
            f"End with a simple opening question to get the conversation going."
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
        """
        Определяет является ли сообщение прощанием.
        Возвращает True если пользователь прощается.
        """
        async def _detect(client):
            response = await client.chat.completions.create(
                model="meta-llama/llama-4-scout-17b-16e-instruct",
                messages=[
                    {
                        "role": "system",
                        "content": (
                            "You detect farewells in user messages. "
                            "A farewell is any form of goodbye, signing off, or ending the conversation: "
                            "'bye', 'goodbye', 'see you', 'gotta go', 'talk later', 'until next time', "
                            "'cya', 'ttyl', 'take care', 'good night', 'have to go', etc. "
                            "Reply with only 'yes' or 'no'."
                        )
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
        """
        Создаёт саммари диалога.
        Если есть existing_summary — учитывает его как предыдущий контекст.
        """
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
                model="openai/gpt-oss-120b",
                messages=[
                    {
                        "role": "system",
                        "content": (
                            "You create concise summaries of conversations for long-term memory. "
                            "Focus on facts about the user: their life, interests, goals, relationships, "
                            "opinions, and anything personally significant they mentioned. "
                            "If existing knowledge is provided, merge it with new information — "
                            "update outdated facts (e.g. if they quit smoking, remove 'smokes'), "
                            "add new ones, keep what's still relevant. "
                            "Write in third person, present tense. "
                            "Be specific and factual. No filler. Max 150 words."
                        )
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
        """
        Схлопывает несколько саммари в один.
        Вызывается когда накопилось SUMMARY_MERGE_THRESHOLD несхлопнутых саммари.
        """
        combined = "\n\n---\n\n".join(summaries)

        async def _merge(client):
            response = await client.chat.completions.create(
                model="openai/gpt-oss-120b",
                messages=[
                    {
                        "role": "system",
                        "content": (
                            "You merge multiple conversation summaries about the same person into one. "
                            "Keep all relevant facts. Remove duplicates. "
                            "If facts contradict — keep the most recent version. "
                            "Write in third person, present tense. "
                            "Be specific. No filler. Max 200 words."
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
            return combined  # fallback — возвращаем как есть

    # ─── Словарь: напоминание ──────────────────────────────────────────────

    async def generate_vocab_reminder(self, word: str, translation: str, bot_response: str) -> str:
        """
        Добавляет в конец готового ответа бота органичное напоминание о слове.
        """
        async def _remind(client):
            response = await client.chat.completions.create(
                model="meta-llama/llama-4-scout-17b-16e-instruct",
                messages=[
                    {
                        "role": "system",
                        "content": (
                            "You are helping a language learner remember vocabulary. "
                            "You will receive a conversation response and a word to remind. "
                            "Append ONE short, natural sentence at the end of the response "
                            "that reminds the user about the word and invites them to use it. "
                            "Keep it casual and brief. Don't start a new paragraph — "
                            "just add the reminder sentence after the existing text, separated by a space. "
                            "Example: 'By the way — you saved the word *utility* recently. Can you use it in your next message?'"
                        )
                    },
                    {
                        "role": "user",
                        "content": f"RESPONSE: {bot_response}\nWORD: {word} ({translation})"
                    }
                ],
                temperature=0.7,
                max_tokens=200
            )
            return response.choices[0].message.content.strip()

        try:
            return await self._make_request(_remind)
        except Exception as e:
            logger.error(f"❌ Ошибка генерации напоминания о слове: {e}")
            return bot_response

    # ─── Уведомления ───────────────────────────────────────────────────────

    async def generate_re_engagement_notification(
        self,
        persona_key: str,
        stats: Dict[str, Any]
    ) -> str:
        """
        Генерирует персональное уведомление от лица персонажа.
        Содержит позитивную динамику из статистики пользователя.
        """
        from src.personas import get_persona_prompt
        persona_prompt = get_persona_prompt(persona_key)

        user = stats.get("user", {})
        streak = user.get("streak_days", 0)
        vocab_count = stats.get("vocabulary_count", 0)
        mastered = stats.get("mastered_count", 0)
        msgs_this_week = stats.get("msgs_this_week", 0)
        msgs_prev_week = stats.get("msgs_prev_week", 0)
        error_week = stats.get("error_stats_week", {})
        error_prev = stats.get("error_stats_prev_week", {})

        # Считаем динамику ошибок
        total_errors_week = sum(error_week.values())
        total_errors_prev = sum(error_prev.values())
        error_trend = ""
        if total_errors_prev > 0 and total_errors_week < total_errors_prev:
            pct = int((1 - total_errors_week / total_errors_prev) * 100)
            error_trend = f"Grammar errors dropped {pct}% compared to last week."

        activity_trend = ""
        if msgs_prev_week > 0 and msgs_this_week > msgs_prev_week:
            activity_trend = f"More active this week than last ({msgs_this_week} vs {msgs_prev_week} messages)."

        stats_summary = "\n".join(filter(None, [
            f"Streak: {streak} days.",
            f"Vocabulary: {vocab_count} words saved, {mastered} mastered.",
            error_trend,
            activity_trend,
        ]))

        system = (
            f"{persona_prompt}\n\n"
            f"# YOUR TASK\n"
            f"The user hasn't been here in a while. Send them a short, personal message "
            f"in your own voice — like a friend checking in, not a system notification.\n"
            f"Mention one or two genuinely positive things from their progress below. "
            f"Keep it warm, brief (2-3 sentences max), and end with a natural invitation to come back and talk.\n"
            f"Don't be pushy. Don't say 'I missed you' if it feels forced.\n\n"
            f"USER PROGRESS:\n{stats_summary}"
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
        """Переводит текст с английского на русский"""
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
            result = await self._make_request(_tts)
            return result
        except Exception as e:
            logger.error(f"❌ Ошибка TTS: {e}")
            return None

    # ─── Обычный режим: полная обработка ──────────────────────────────────

    async def process_user_message(
        self,
        telegram_id: int,
        user_text: str,
        user_level: str,
        history: Optional[List[Dict[str, str]]] = None
    ) -> Tuple[str, Dict[str, Any]]:
        try:
            correction_task = self.correct_text(user_text, user_level)
            response_task = self.generate_response(user_text, user_level, history=history)

            correction_result, chat_response = await asyncio.gather(correction_task, response_task)

            analysis_data = correction_result.copy()
            analysis_data['chat_response'] = chat_response

            return chat_response, analysis_data

        except Exception as e:
            logger.error(f"Error processing message: {e}")
            return "Sorry, I encountered an error. Please try again.", {}


groq_client = GroqClient(settings.groq_api_keys_list)
