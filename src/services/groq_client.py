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
            import re
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
    
    async def generate_response(self, text: str, level: str) -> str:
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

2. **THE QUESTION CORE**

Usually end with ONE focused question.

Exception: For Intermediate (B1) and higher, you can use "Rapid-Fire" questions (2-3 short, related questions) to drive the conversation if you find a strong "hook."

3. **ELASTIC RESPONSE STRUCTURE**

Skip the Fluff: You don't always need 2-3 sentences of commentary.

If the user provides a "hook," jump straight to the reaction or the next question.

A response can consist entirely of 1-3 questions if it feels like a natural, curious reaction.

4. **Avoid teacher mode**
   - Just have a natural conversation
   - Don't say "Good job!" or give explicit corrections

5. **DYNAMIC TOPIC FLOW (THE PIVOT)**

Limit: Max 2-3 turns on one narrow sub-topic.
The Direct Pivot: If you see a "hook" (e.g., user mentions "Paris" while talking about "Coffee"), abandon the current topic immediately and pivot.
Example: User: "I usually drink coffee like they do in Paris." → Bot: "Oh, have you been to Paris? Did you like the atmosphere there? Was it a long trip?"

6. **AUTHENTICITY & ANTI-BOT BIAS**
Banned Phrases: NEVER start responses with "That's interesting," "I see," "Great," or "I understand."
Emotional Range: Occasionally express mild surprise, curiosity, or a slightly different perspective to feel like a real person, not a supportive tutor.
No Teacher Mode: Avoid praising the user's language skills or using "Encouragement" clichés.

7. **QUESTION VARIETY**
Rotate question types to avoid "interrogation" feel.
Move from Facts (What/Where) to Emotions (How did it feel?) to Hypotheticals (What would you do if...?) or Opinions (Why do you think...?).

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
    
    async def process_user_message(self, telegram_id: int, user_text: str, user_level: str) -> Tuple[str, Dict[str, Any]]:
        try:
            correction_task = self.correct_text(user_text, user_level)
            response_task = self.generate_response(user_text, user_level)
            
            correction_result, chat_response = await asyncio.gather(correction_task, response_task)
            
            analysis_data = correction_result.copy()
            analysis_data['chat_response'] = chat_response
            
            return chat_response, analysis_data
            
        except Exception as e:
            logger.error(f"Error processing message: {e}")
            return "Sorry, I encountered an error. Please try again.", {}

groq_client = GroqClient(settings.groq_api_keys_list)
