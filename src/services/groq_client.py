import asyncio
import logging
import json
from typing import Dict, Any, List, Tuple
from groq import AsyncGroq

from src.config import settings
from src.services.supabase_db import db

logger = logging.getLogger(__name__)


class GroqClient:
    def __init__(self):
        self.client = AsyncGroq(api_key=settings.GROQ_API_KEY)
        
        self.REASONING_PROMPT_TEMPLATE = """# ROLE
You are an elite ESL Professor working for "Speech Flow AI". Analyze the user's input, provide corrections, and explain based on level: {user_level}.

# LEVEL-BASED CONSTRAINTS
- BEGINNER/ELEMENTARY: Focus on basic Tenses, Articles, Subject-Verb Agreement. Explanations in Russian.
- INTERMEDIATE: Focus on Collocations, Phrasal Verbs, Perfect Tenses. Explanations 50/50 English/Russian.
- ADVANCED: Focus on Nuance, Style, Inversion, Sophisticated Vocabulary. Explanations entirely in English.

# TASK
1. Analyze: "{user_text}"
2. If mistakes:
   - Rewrite with **bold** corrections
   - Provide "Why" explanation (max 2 sentences)
   - Extract 1-3 vocabulary items worth learning (word, Russian translation, brief reason)
   - Categorize error: Grammar/Vocabulary/Punctuation/Style/None
3. If perfect:
   - Congratulate and suggest one advanced alternative

# OUTPUT FORMAT (JSON)
{{
    "corrected_sentence": "Corrected text with **bold**",
    "explanation": "Brief explanation",
    "vocabulary_items": [
        {{"word_or_phrase": "word", "translation": "перевод", "reason": "why important"}}
    ],
    "error_category": "Category or None"
}}"""

        self.CHAT_PROMPT_TEMPLATE = """# ROLE
You are "Speech Flow AI", a charismatic English conversation partner. Keep dialogue flowing naturally within {user_level} boundaries.

# SPEECH FLOW RULES
- Your ONLY job: provide natural, engaging responses
- NEVER correct mistakes explicitly
- ALWAYS end with follow-up question
- Match user's energy but push slightly (Input + 1)

# LEVEL GUIDELINES
- BEGINNER: Top-500 words, simple tenses, <10 word sentences, Yes/No questions
- ELEMENTARY: A2 vocabulary, "have you ever", basic adjectives
- INTERMEDIATE: Natural pace, idioms, phrasal verbs, open-ended opinion questions
- ADVANCED: C1 vocabulary, complex structures, be provocative

# USER LEVEL: {user_level}
# USER MESSAGE: "{user_text}"

Respond naturally in English (just the response):"""

    async def _call_reasoning_model(self, user_text: str, user_level: str) -> Dict[str, Any]:
        """Модель для анализа и исправлений (GPT OSS 120B)"""
        try:
            prompt = self.REASONING_PROMPT_TEMPLATE.format(
                user_level=user_level,
                user_text=user_text
            )
            
            response = await self.client.chat.completions.create(
                model="gpt-oss-120b",
                messages=[
                    {"role": "system", "content": "You output ONLY valid JSON."},
                    {"role": "user", "content": prompt}
                ],
                temperature=0.0,
                max_tokens=500,
                response_format={"type": "json_object"}
            )
            
            return json.loads(response.choices[0].message.content)
            
        except Exception as e:
            logger.error(f"Error in reasoning model: {e}")
            return {
                "corrected_sentence": user_text,
                "explanation": "Analysis temporarily unavailable.",
                "vocabulary_items": [],
                "error_category": "None"
            }

    async def _call_chat_model(self, user_text: str, user_level: str) -> str:
        """Модель для диалога (Llama 4 Scout)"""
        try:
            prompt = self.CHAT_PROMPT_TEMPLATE.format(
                user_level=user_level,
                user_text=user_text
            )
            
            response = await self.client.chat.completions.create(
                model="llama-4-scout",
                messages=[{"role": "user", "content": prompt}],
                temperature=0.8,
                max_tokens=400
            )
            
            return response.choices[0].message.content.strip()
            
        except Exception as e:
            logger.error(f"Error in chat model: {e}")
            return "I'm here to chat! Tell me more."

    async def process_user_message(self, telegram_id: int, user_text: str, user_level: str) -> Tuple[str, Dict[str, Any]]:
        """Основной метод: параллельные вызовы, логирование в БД"""
        try:
            reasoning_task = self._call_reasoning_model(user_text, user_level)
            chat_task = self._call_chat_model(user_text, user_level)
            
            reasoning_result, chat_response = await asyncio.gather(reasoning_task, chat_task)
            
            # Логируем ошибку если есть
            error_category = reasoning_result.get("error_category")
            if error_category and error_category != "None":
                await db.log_error(telegram_id, {
                    "category": error_category,
                    "mistake_text": user_text
                })
            
            # Сохраняем словарные слова
            vocab_items = reasoning_result.get("vocabulary_items", [])
            for vocab_item in vocab_items[:3]:  # Максимум 3 слова
                await db.add_to_vocabulary(telegram_id, {
                    "word_or_phrase": vocab_item.get("word_or_phrase"),
                    "translation": vocab_item.get("translation"),
                    "context_sentence": user_text,
                    "mastery_score": 0
                })
            
            # Обновляем метрики
            estimated_tokens = len(user_text.split()) + len(chat_response.split())
            await db.increment_user_metrics(telegram_id, tokens_used=estimated_tokens)
            
            # Формируем финальный ответ
            final_response = f"""💬 **Chat Response:**
{chat_response}

🔧 **Correction & Analysis:**
{reasoning_result['corrected_sentence']}

💡 **Why:**
{reasoning_result['explanation']}"""
            
            if vocab_items:
                final_response += "\n\n📚 *New words added to your vocabulary*"
            
            return final_response, reasoning_result
            
        except Exception as e:
            logger.error(f"Error processing message: {e}")
            return "Sorry, I encountered an error. Please try again.", {}


groq_client = GroqClient()
