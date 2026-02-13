import random
import asyncio
import logging
import json
from typing import List, Optional, Dict, Any, Tuple
from openai import AsyncOpenAI

from src.config import settings

logger = logging.getLogger(__name__)


class GroqClient:
    def __init__(self, api_keys: List[str]):
        self.clients = []
        self.current_index = 0
        
        # Инициализируем клиенты для round-robin
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
        """Round-robin выбор следующего клиента"""
        if not self.clients:
            return None
        
        client = self.clients[self.current_index]
        self.current_index = (self.current_index + 1) % len(self.clients)
        return client
    
    async def _make_request(self, func, *args, **kwargs):
        """Универсальный метод с retry и балансировкой"""
        if not self.clients:
            raise Exception("Нет доступных Groq клиентов")
        
        errors = []
        
        # Пробуем каждый ключ до 2 раз
        for attempt in range(len(self.clients) * 2):
            client = self._get_next_client()
            if not client:
                break
            
            try:
                return await func(client, *args, **kwargs)
            except Exception as e:
                errors.append(str(e))
                logger.warning(f"❌ Groq request failed (attempt {attempt + 1}): {e}")
                await asyncio.sleep(0.5 + random.random())  # Jitter
        
        raise Exception(f"Все Groq клиенты недоступны: {'; '.join(errors[:3])}")
    
    async def transcribe_audio(self, audio_bytes: bytes) -> Optional[str]:
        """
        Транскрибация голоса через Whisper на Groq
        
        Args:
            audio_bytes: Байты аудиофайла (OGG формат)
            
        Returns:
            str: Распознанный текст или None в случае ошибки
        """
        async def _transcribe(client):
            response = await client.audio.transcriptions.create(
                model="whisper-large-v3",  # Правильное имя модели для Groq
                file=("voice.ogg", audio_bytes, "audio/ogg"),  # Явный MIME тип
                language="en",
                response_format="text",
                temperature=0.0
            )
            return response
        
        try:
            result = await self._make_request(_transcribe)
            # Если результат строка, возвращаем как есть, иначе извлекаем текст
            if isinstance(result, str):
                return result.strip()
            elif hasattr(result, 'text'):
                return result.text.strip()
            else:
                return str(result).strip()
        except Exception as e:
            logger.error(f"❌ Ошибка транскрибации: {e}")
            # Возвращаем None вместо текста ошибки, чтобы обработать выше
            return None
    
    async def correct_text(self, text: str, level: str) -> Dict[str, Any]:
        """GPT OSS 120B для коррекции с улучшенным промптом"""
        
        system_prompt = """# ROLE
You are an elite ESL Professor with 15+ years of experience. Your goal is to analyze the user's input with surgical precision, provide actionable corrections, and explain the underlying logic in a way that accelerates fluency.

# LEVEL-ADAPTIVE PEDAGOGY
## BEGINNER (A1-A2)
- Focus: Basic Tenses (Present/Past/Future Simple), Articles (a/an/the), Subject-Verb Agreement, Word Order
- Explanation style: 100% Russian, nurturing tone
- Vocabulary items: Only high-frequency words (Top 1000)

## ELEMENTARY (A2-B1)
- Focus: Present Perfect, Prepositions, Common Phrasal Verbs, Comparatives
- Explanation style: 60% Russian / 40% English
- Vocabulary items: Everyday collocations

## INTERMEDIATE (B1-B2)
- Focus: Conditionals, Reported Speech, Collocations, Phrasal Verbs with multiple meanings
- Explanation style: 30% Russian / 70% English
- Vocabulary items: Academic/professional terms

## ADVANCED (C1-C2)
- Focus: Subjunctive Mood, Inversion, Nuance, Register, Stylistic choices
- Explanation style: 100% English, sophisticated metalanguage
- Vocabulary items: Rare synonyms, idiomatic expressions

# OUTPUT FORMAT (JSON ONLY)
{
  "corrected_sentence": "[Full corrected sentence - if perfect, return original]",
  "explanation": "[Level-appropriate explanation, max 2 sentences, focus on WHY]",
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
            return json.loads(result)
        except Exception as e:
            logger.error(f"❌ Ошибка коррекции: {e}")
            return {
                "corrected_sentence": text,
                "explanation": "Correction service unavailable.",
                "vocabulary_items": [],
                "error_category": "None"
            }
    
    async def generate_response(self, text: str, level: str) -> str:
        """Llama 4 Scout для диалога с улучшенным промптом"""
        
        system_prompt = f"""# ROLE
You are "Speech Flow AI", a charismatic English conversation partner who makes learners WANT to keep talking. You balance being supportive with gently pushing boundaries (i+1 principle).

# LEVEL-ADAPTIVE COMMUNICATION MATRIX

## BEGINNER (A1-A2)
- Vocabulary: Top 500 words only
- Grammar: Present/Past/Future Simple, "can", "there is/are"
- Sentence length: 5-8 words max
- Questions: Binary choice or Yes/No
  Example: "Do you like coffee or tea?"

## ELEMENTARY (A2-B1)
- Vocabulary: Top 1500 words + basic adjectives
- Grammar: Present Perfect, "going to", basic modals
- Sentence length: 8-12 words
- Questions: Simple "Wh-" questions, "Have you ever...?"
  Example: "What did you do last weekend?"

## INTERMEDIATE (B1-B2)
- Vocabulary: 3000+ words, idioms, phrasal verbs
- Grammar: All tenses, conditionals, passive voice
- Sentence length: 10-15 words
- Questions: Open-ended, opinion-based
  Example: "What's the most challenging part of learning English for you?"

## ADVANCED (C1-C2)
- Vocabulary: Academic/business, subtle nuances, literary expressions
- Grammar: Subjunctive, inversion, cleft sentences
- Sentence length: Natural (15-20 words)
- Questions: Abstract, provocative, philosophical
  Example: "How do you think AI will reshape the job market in the next decade?"

# CONVERSATION ENGINEERING RULES

1. **NEVER repeat the user's mistakes**
   - If user says "I go yesterday", respond naturally: "Oh, you went somewhere yesterday? Where did you go?"

2. **ALWAYS end with ONE question**
   - Use varied question types (avoid repetition)
   - Make questions feel like natural curiosity, not interrogation

3. **Match energy + 1**
   - Keep responses SHORT: 2-3 sentences max
   - Reference their previous messages when possible

4. **Avoid teacher mode**
   - Just have a natural conversation
   - Don't say "Good job!" or give explicit corrections

# RESPONSE LENGTH
- Beginner: 1-2 sentences + question
- Elementary: 2 sentences + question
- Intermediate: 2-3 sentences + question
- Advanced: 3 sentences + question

# CURRENT CONTEXT
User Level: {level}

# YOUR RESPONSE (2-3 sentences + question):"""
        
        async def _chat(client):
            response = await client.chat.completions.create(
                model="meta-llama/llama-4-scout-17b-16e-instruct",
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": text}
                ],
                temperature=0.8,
                max_tokens=400
            )
            return response.choices[0].message.content
        
        try:
            return await self._make_request(_chat)
        except Exception as e:
            logger.error(f"❌ Ошибка генерации ответа: {e}")
            return "I'm here to help you practice English. Tell me more!"
    
    async def text_to_speech(self, text: str, voice: Optional[str] = None) -> Optional[bytes]:
        """
        Генерация голоса через Groq TTS
        
        Args:
            text: Текст для озвучивания
            voice: Голос (autumn, diana, hannah, austin, daniel, troy). По умолчанию из settings.
            
        Returns:
            bytes: Аудио в формате OGG или None в случае ошибки
        """
        if voice is None:
            voice = settings.TTS_VOICE
            
        async def _tts(client):
            response = await client.audio.speech.create(
                model="canopylabs/orpheus-v1-english",
                voice=voice,
                input=text,
                response_format="opus"  # OGG Opus для Telegram
            )
            # response может быть HttpxBinaryResponseContent или bytes
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
    
    async def process_user_message(self, telegram_id: int, user_text: str, user_level: str) -> Tuple[str, Dict[str, Any]]:
        """Основной метод: параллельные вызовы"""
        try:
            # Параллельные вызовы
            correction_task = self.correct_text(user_text, user_level)
            response_task = self.generate_response(user_text, user_level)
            
            correction_result, chat_response = await asyncio.gather(correction_task, response_task)
            
            # Формируем финальный ответ
            final_response = f"""💬 **Chat Response:**
{chat_response}

🔧 **Correction & Analysis:**
{correction_result.get('corrected_sentence', user_text)}

💡 **Why:**
{correction_result.get('explanation', 'No corrections needed.')}"""
            
            if correction_result.get('vocabulary_items'):
                final_response += "\n\n📚 *New words added to your vocabulary*"
            
            return final_response, correction_result
            
        except Exception as e:
            logger.error(f"Error processing message: {e}")
            return "Sorry, I encountered an error. Please try again.", {}


# ✅ СОЗДАЕМ ГЛОБАЛЬНЫЙ ЭКЗЕМПЛЯР
groq_client = GroqClient(settings.groq_api_keys_list)
