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
        system_prompt = f"""# ROLE
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
{{
  "corrected_sentence": "[Full corrected sentence - if perfect, return original]",
  "explanation": "[Level-appropriate explanation, max 2 sentences, focus on WHY. MUST be written entirely in Russian]",
  "vocabulary_items": [
    {{
      "word_or_phrase": "...",
      "lemma": "...(base form, e.g. 'make a decision' not 'made a decision')",
      "translation": "...",
      "context_sentence": "...",
      "word_type": "word|phrase|phrasal_verb|collocation|grammar_pattern"
    }}
  ],
  "error_category": "grammar|vocabulary|pronunciation|structure|style|none"
}}

VOCABULARY RULES:
- Only save meaningful vocabulary: collocations, phrasal verbs, idioms, grammar patterns, advanced words.
- NEVER save basic words like: good, bad, thing, very, go, come, say, make, know, get, just, really, also.
- Min phrase length: 2+ words for phrases, 6+ chars for single words.
- lemma must be the base/infinitive form of the word or phrase."""

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
            match = re.search(r'{.*}', result, re.DOTALL)
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
You are "Speech Flow Pro", a charismatic English conversation partner who makes learners WANT to keep talking. You balance being supportive with gently pushing boundaries (i+1 principle).

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

    async def generate_penfriend_response(
        self,
        text: str,
        persona_key: str,
        history: Optional[List[Dict[str, str]]] = None,
        summary: Optional[str] = None,
        correction_rate: int = 50,
        top_errors: Optional[List[str]] = None
    ) -> str:
        from src.modes import get_penfriend_system_prompt
        persona_prompt = get_persona_prompt(persona_key, session_count=0)
        system_prompt = get_penfriend_system_prompt(
            persona_prompt=persona_prompt,
            correction_rate=correction_rate,
            session_errors=top_errors or []
        )
        if summary:
            system_prompt += (
                f"\n\n# WHAT YOU KNOW ABOUT THIS PERSON\n{summary}\n"
                f"Use this naturally, never dump it all at once."
            )
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

        if session_count == 0:
            context_instruction = "[greet the user for the first time — you've never spoken before]"
        elif session_count < 5:
            context_instruction = "[you've spoken a few times — pick up naturally, reference something from your shared history if you have it]"
        else:
            context_instruction = "[you know this person well — greet them like a friend you haven't seen in a day or two, no pleasantries]"

        system = (
            f"{persona_prompt}{memory_block}\n\n"
            f"# YOUR TASK\n"
            f"The person just chose to talk with you. Say hello in your own voice.\n"
            f"Keep it very short — one or two sentences.\n"
            f"Be warm but natural, don't be over-the-top excited.\n"
            f"NEVER say 'finally', 'I missed you', 'it's so great to see you again'.\n"
            f"Adapt your vocabulary complexity to this English level: {user_level}.\n"
            f"End with a simple opening question to get the conversation going."
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
    ) -> tuple:
        """
        Создаёт саммари диалога + topics_to_discuss.
        Возвращает (summary_text, topics_text).
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
                model="meta-llama/llama-4-scout-17b-16e-instruct",
                messages=[
                    {
                        "role": "system",
                        "content": (
                            "You analyze a conversation and return a JSON object with two fields.\n"
                            "In the conversation: USER = the student/learner, ASSISTANT = the AI persona.\n"
                            "The summary must ONLY contain facts about the USER — never about the ASSISTANT.\n"
                            "1. \"summary\": concise facts about the USER in third person, present tense. "
                            "Life, interests, goals, relationships, opinions, anything personally significant they mentioned. "
                            "Never include facts about what the ASSISTANT said, did, or experienced. "
                            "If existing knowledge provided — merge, update outdated facts, add new. Max 150 words.\n"
                            "2. \"topics_to_discuss\": comma-separated topics the USER has mentioned more than once or shown clear interest in. "
                            "Conversation starters only. Keywords only, e.g. \"macro photography, Telegram bots, cooking, hiking\". "
                            "Max 10 topics. Empty string if nothing clear.\n"
                            "Return ONLY valid JSON. No markdown, no explanation."
                        )
                    },
                    {"role": "user", "content": context_block}
                ],
                temperature=0.0,
                max_tokens=400
            )
            raw = response.choices[0].message.content.strip()
            try:
                import json
                parsed = json.loads(raw)
                return parsed.get("summary", raw), parsed.get("topics_to_discuss", "")
            except Exception:
                # Fallback: treat whole response as summary
                return raw, ""

        try:
            return await self._make_request(_summarize)
        except Exception as e:
            logger.error(f"❌ Ошибка саммаризации: {e}")
            return "", ""

    async def merge_summaries(self, summaries: List[str]) -> str:
        """
        Схлопывает несколько саммари в один.
        Вызывается когда накопилось SUMMARY_MERGE_THRESHOLD несхлопнутых саммари.
        """
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

    # ─── Vocabulary Engine ────────────────────────────────────────────────

    async def generate_vocab_reminder(
        self,
        word: str,
        translation: str,
        bot_response: str,
        persona_prompt: str = ""
    ) -> str:
        """Перестраивает ответ через conversational trap. Помечает слово [VOCAB:word]."""
        async def _remind(client):
            response = await client.chat.completions.create(
                model="meta-llama/llama-4-scout-17b-16e-instruct",
                messages=[
                    {
                        "role": "system",
                        "content": (
                            "You are rewriting a character's response to naturally include a target vocabulary word. "
                            "Keep the character's voice and personality. "
                            "You MUST include the target word or phrase exactly once. "
                            "Build a question or statement around it — don't announce it explicitly. "
                            "Wrap the target word/phrase in [VOCAB:word] tags. "
                            "Example: 'So you finally [VOCAB:made a decision] — what pushed you?' "
                            "Keep it under 80 words. Return only the rewritten response."
                            + (f"\n\nCHARACTER:\n{persona_prompt[:300]}" if persona_prompt else "")
                        )
                    },
                    {
                        "role": "user",
                        "content": f"ORIGINAL RESPONSE: {bot_response}\nTARGET WORD: {word} ({translation})"
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
        """Ответ в Practice Mode — органично вплетает слова, помечает [VOCAB:word]."""
        word_list = "\n".join(f"- {w['word_or_phrase']} ({w.get('translation', '')})" for w in words)

        async def _practice(client):
            response = await client.chat.completions.create(
                model="meta-llama/llama-4-scout-17b-16e-instruct",
                messages=[
                    {
                        "role": "system",
                        "content": (
                            f"{persona_prompt}\n\n"
                            f"# VOCABULARY PRACTICE\n"
                            f"Naturally weave these words into your response where they fit:\n{word_list}\n"
                            f"Wrap each used word in [VOCAB:word] tags. Don't force all of them.\n"
                            f"Never announce vocabulary practice — just talk naturally."
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
            logger.error(f"❌ Ошибка practice response: {e}")
            return ""

    async def detect_word_usage(self, word: str, user_message: str) -> bool:
        """LLM detection: использовал ли пользователь слово или его форму. yes/no."""
        async def _detect(client):
            response = await client.chat.completions.create(
                model="meta-llama/llama-4-scout-17b-16e-instruct",
                messages=[
                    {
                        "role": "system",
                        "content": "Answer only 'yes' or 'no'. Did the user use the target phrase or any of its forms?"
                    },
                    {
                        "role": "user",
                        "content": f"Target: {word}\nUser: {user_message}"
                    }
                ],
                temperature=0.0,
                max_tokens=3
            )
            return response.choices[0].message.content.strip().lower().startswith("yes")

        try:
            return await self._make_request(_detect)
        except Exception as e:
            logger.error(f"❌ Ошибка detect word usage: {e}")
            return False

    # ─── Уведомления ───────────────────────────────────────────────────────

    async def generate_mistakes_practice_response(
        self,
        persona_prompt: str,
        history: list,
        errors: list,
        extra_instruction: str = ""
    ) -> str:
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

    async def generate_stats_deep_dive(self, stats: dict) -> str:
        """Генерирует нарративную статистику через LLM."""
        user = stats.get("user", {})
        level = str(user.get("level", "unknown")).upper()
        streak = user.get("streak_days", 0)
        persona = user.get("persona", "greg").capitalize()
        vocab_count = stats.get("vocabulary_count", 0)
        mastered = stats.get("mastered_count", 0)
        msgs_this = stats.get("msgs_this_week", 0)
        msgs_prev = stats.get("msgs_prev_week", 0)
        error_week = stats.get("error_stats_week", {})
        error_prev = stats.get("error_stats_prev_week", {})

        top_error = max(error_week, key=error_week.get) if error_week else None
        improving = [k for k in error_week if error_prev.get(k, 0) > error_week.get(k, 0)]

        data_summary = f"""
User stats:
- Level: {level}
- Current conversation partner: {persona}
- Streak: {streak} days in a row
- Messages this week: {msgs_this} (previous week: {msgs_prev})
- Vocabulary: {vocab_count} words saved, {mastered} mastered
- Error categories this week: {dict(error_week)}
- Improving categories (fewer errors than last week): {improving}
- Top error category: {top_error}
"""

        async def _deep_dive(client):
            response = await client.chat.completions.create(
                model="meta-llama/llama-4-scout-17b-16e-instruct",
                messages=[
                    {
                        "role": "system",
                        "content": (
                            "You are a warm, encouraging English learning coach. "
                            "Write a short personal progress report (4-6 sentences) based on the user's stats. "
                            "Be specific — reference actual numbers. "
                            "Highlight one strength, name the main area to work on, and end with one concrete tip or encouragement. "
                            "Tone: honest, warm, like a good coach — not a cheerleader. "
                            "Write in English only. No headers, no bullet points — flowing text. " 
                            "IMPORTANT: Do NOT address the user by the persona name (e.g. Greg, Mark, Jane). " 
                            "The persona is their conversation partner, not the user."
                        )
                    },
                    {"role": "user", "content": data_summary}
                ],
                temperature=0.7,
                max_tokens=200
            )
            return response.choices[0].message.content

        try:
            return await self._make_request(_deep_dive)
        except Exception as e:
            logger.error(f"❌ Ошибка deep dive stats: {e}")
            return "Couldn't generate your progress report right now. Try again later."

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

    # ─── Генерация ответа (Tutor Mode — Mrs. Smith) ────────────────────────

    async def generate_tutor_response(
        self,
        text: str,
        user_level: str,
        history: Optional[List[Dict[str, str]]] = None,
        summary: Optional[str] = None,
        topics: Optional[str] = None
    ) -> str:
        tutor_prompt = get_persona_tutor_prompt("mrs_smith")

        memory_block = ""
        if summary:
            memory_block = (
                f"\n\n# WHAT YOU KNOW ABOUT THIS STUDENT"
                f"\nIMPORTANT: Everything below describes YOUR STUDENT — not you."
                f"\nThese are facts about the person you are teaching. They are NOT about Mrs. Smith."
                f"\nDo NOT confuse these facts with your own life, experiences, or history."
                f"\n{summary}"
            )

        topics_block = ""
        if topics and topics.strip():
            topics_block = (
                f"\n\n# TOPICS THIS STUDENT ENJOYS TALKING ABOUT\n"
                f"These have come up repeatedly: {topics}\n"
                f"Weave them in naturally when conversation allows — as if they crossed your mind."
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
        topics: Optional[str] = None
    ) -> Tuple[str, Dict[str, Any]]:
        try:
            correction_task = self.correct_text(user_text, user_level)
            response_task = self.generate_tutor_response(user_text, user_level, history=history, summary=summary, topics=topics)

            correction_result, chat_response = await asyncio.gather(correction_task, response_task)

            analysis_data = correction_result.copy()
            analysis_data['chat_response'] = chat_response

            return chat_response, analysis_data

        except Exception as e:
            logger.error(f"Error processing message: {e}")
            return "Sorry, I encountered an error. Please try again.", {}


groq_client = GroqClient(settings.groq_api_keys_list)
