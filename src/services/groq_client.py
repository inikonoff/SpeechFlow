# src/services/groq_client.py
import random
import asyncio
import logging
from typing import List, Optional
from openai import AsyncOpenAI

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
    
    async def transcribe_audio(self, audio_bytes: bytes) -> str:
        """Транскрибация голоса через Whisper v3 Turbo на Groq"""
        async def _transcribe(client):
            response = await client.audio.transcriptions.create(
                model="whisper-large-v3-turbo",
                file=("audio.ogg", audio_bytes, "audio/ogg"),
                language="en",  # Для Speech Flow - английский
                response_format="text",
                temperature=0.0
            )
            return response
        
        try:
            result = await self._make_request(_transcribe)
            return result.strip()
        except Exception as e:
            logger.error(f"❌ Ошибка транскрибации: {e}")
            return f"[Transcription error: {str(e)[:100]}]"
    
    async def correct_text(self, text: str, level: str) -> dict:
        """GPT OSS 120B для коррекции"""
        async def _correct(client):
            response = await client.chat.completions.create(
                model="gpt-oss-120b",
                messages=[
                    {"role": "system", "content": "You are an ESL professor. Output JSON only."},
                    {"role": "user", "content": f"Level: {level}\nText: {text}\nCorrect and explain."}
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
        """Llama 4 Scout для диалога"""
        async def _chat(client):
            response = await client.chat.completions.create(
                model="llama-4-scout",
                messages=[
                    {"role": "system", "content": f"You are Speech Flow AI. Level: {level}"},
                    {"role": "user", "content": text}
                ],
                temperature=0.8
            )
            return response.choices[0].message.content
        
        try:
            return await self._make_request(_chat)
        except Exception as e:
            logger.error(f"❌ Ошибка генерации ответа: {e}")
            return "I'm here to help you practice English. Tell me more!"
