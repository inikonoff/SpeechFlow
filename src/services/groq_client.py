#!/usr/bin/env python3
"""
TTS Server with Grok
Реализует streaming TTS только через Grok API
"""

import asyncio
import base64
import json
import logging
import os
import signal
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from typing import AsyncGenerator, Dict, List, Optional

import aiohttp
import uvicorn
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

# ==================== НАСТРОЙКА ЛОГГЕРА ====================
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# ==================== КОНФИГУРАЦИЯ ====================
GROK_API_KEY = os.environ.get("GROK_API_KEY", "")
GROK_API_URL = "https://api.x.ai/v1/audio/speech"
GROK_VOICE = "male"  # или "female", в зависимости от доступных голосов

MAX_WORKERS = 4  # Максимальное количество параллельных потоков
CHUNK_SIZE = 4096  # Размер чанка для streaming (не используется для Grok, но оставим)

# ==================== МОДЕЛИ ДАННЫХ ====================
class TTSRequest(BaseModel):
    text: str = Field(..., description="Текст для озвучивания")
    voice: Optional[str] = Field(GROK_VOICE, description="Голос для озвучивания")
    speed: Optional[float] = Field(1.0, description="Скорость речи (0.25-4.0)")

class StreamingTTSRequest(BaseModel):
    sentences: List[str] = Field(..., description="Список предложений для последовательного озвучивания")
    voice: Optional[str] = Field(GROK_VOICE, description="Голос для озвучивания")
    speed: Optional[float] = Field(1.0, description="Скорость речи (0.25-4.0)")

# ==================== ИНИЦИАЛИЗАЦИЯ FASTAPI ====================
app = FastAPI(title="TTS Server with Grok")

# ==================== ОБРАБОТЧИКИ СИГНАЛОВ ====================
def signal_handler(sig, frame):
    """Обработка сигналов завершения"""
    logger.info(f"Received signal {sig}, shutting down...")
    sys.exit(0)

signal.signal(signal.SIGTERM, signal_handler)
signal.signal(signal.SIGINT, signal_handler)
logger.info("✅ Signal handlers registered")

# ==================== ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ ====================
async def generate_speech_grok(text: str, voice: str = GROK_VOICE, speed: float = 1.0) -> bytes:
    """
    Генерация речи через Grok API
    
    Args:
        text: Текст для озвучивания
        voice: Голос
        speed: Скорость речи
        
    Returns:
        bytes: Аудиоданные в формате MP3
    """
    if not GROK_API_KEY:
        logger.error("GROK_API_KEY not set")
        raise HTTPException(status_code=500, detail="Grok API key not configured")
    
    headers = {
        "Authorization": f"Bearer {GROK_API_KEY}",
        "Content-Type": "application/json"
    }
    
    payload = {
        "model": "grok-audio-1",  # или другая доступная модель
        "input": text,
        "voice": voice,
        "response_format": "mp3",
        "speed": speed
    }
    
    logger.info(f"Calling Grok API for text: {text[:50]}...")
    
    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(GROK_API_URL, headers=headers, json=payload) as response:
                if response.status != 200:
                    error_text = await response.text()
                    logger.error(f"Grok API error: {response.status} - {error_text}")
                    raise HTTPException(status_code=response.status, detail=f"Grok API error: {error_text}")
                
                audio_data = await response.read()
                logger.info(f"Received {len(audio_data)} bytes from Grok API")
                return audio_data
                
    except aiohttp.ClientError as e:
        logger.error(f"Grok API connection error: {e}")
        raise HTTPException(status_code=503, detail=f"Grok API connection error: {str(e)}")
    except Exception as e:
        logger.error(f"Unexpected error calling Grok API: {e}")
        raise HTTPException(status_code=500, detail=f"Unexpected error: {str(e)}")

async def generate_and_stream(sentence: str, voice: str, speed: float, queue: asyncio.Queue):
    """Генерация аудио для одного предложения и помещение в очередь"""
    try:
        audio_data = await generate_speech_grok(sentence, voice, speed)
        
        # Создаем заголовок для чанка
        header = json.dumps({
            "type": "audio_chunk",
            "sentence": sentence[:50] + "..." if len(sentence) > 50 else sentence
        }).encode() + b"\n"
        
        # Отправляем заголовок
        await queue.put(header)
        
        # Отправляем аудиоданные
        await queue.put(audio_data)
        
        logger.info(f"✅ Generated audio for sentence: {sentence[:30]}... ({len(audio_data)} bytes)")
        
    except Exception as e:
        logger.error(f"❌ Error generating audio: {e}")
        error_header = json.dumps({
            "type": "error",
            "sentence": sentence[:50] + "..." if len(sentence) > 50 else sentence,
            "error": str(e)
        }).encode() + b"\n"
        await queue.put(error_header)

# ==================== ЭНДПОИНТЫ ====================
@app.get("/health")
async def health_check():
    """Проверка здоровья сервиса"""
    return {
        "status": "healthy",
        "service": "TTS Server with Grok",
        "grok_configured": bool(GROK_API_KEY)
    }

@app.post("/tts")
async def text_to_speech(request: TTSRequest):
    """
    Преобразование текста в речь (одно предложение)
    Возвращает аудиофайл
    """
    try:
        audio_data = await generate_speech_grok(request.text, request.voice, request.speed)
        
        return StreamingResponse(
            iter([audio_data]),
            media_type="audio/mpeg",
            headers={
                "Content-Disposition": f"attachment; filename=speech.mp3"
            }
        )
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Unexpected error in /tts: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/tts/stream")
async def stream_tts(request: StreamingTTSRequest):
    """
    Потоковое преобразование списка предложений в речь
    Возвращает аудиоданные чанками с метаданными
    """
    logger.info(f"🚀 Starting streaming for {len(request.sentences)} sentences")
    
    queue = asyncio.Queue()
    sentences = request.sentences
    voice = request.voice
    speed = request.speed
    
    # Запускаем генерацию для всех предложений параллельно
    tasks = []
    for i, sentence in enumerate(sentences):
        task = asyncio.create_task(
            generate_and_stream(sentence, voice, speed, queue)
        )
        tasks.append(task)
    
    async def stream_generator():
        try:
            # Отправляем метаданные
            metadata = json.dumps({
                "type": "metadata",
                "total_sentences": len(sentences),
                "voice": voice,
                "speed": speed
            }).encode() + b"\n"
            yield metadata
            
            # Ждем завершения всех задач
            pending = len(tasks)
            while pending > 0:
                chunk = await queue.get()
                yield chunk
                
                # Проверяем, не завершились ли все задачи
                if all(t.done() for t in tasks):
                    # Проверяем, все ли данные извлечены из очереди
                    if queue.empty():
                        break
                
            logger.info("📦 Streaming completed")
            
            # Отправляем завершающий маркер
            end_marker = json.dumps({"type": "end"}).encode() + b"\n"
            yield end_marker
            
        except Exception as e:
            logger.error(f"Error in stream_generator: {e}")
            error_marker = json.dumps({
                "type": "error",
                "error": str(e)
            }).encode() + b"\n"
            yield error_marker
    
    return StreamingResponse(
        stream_generator(),
        media_type="application/octet-stream",
        headers={
            "X-Total-Sentences": str(len(sentences)),
            "X-Voice": voice,
            "X-Speed": str(speed)
        }
    )

@app.get("/voices")
async def list_voices():
    """
    Получение списка доступных голосов
    """
    # Grok API пока не предоставляет эндпоинт для списка голосов
    # Возвращаем известные доступные голоса
    voices = [
        {"id": "male", "name": "Male Voice", "description": "Мужской голос"},
        {"id": "female", "name": "Female Voice", "description": "Женский голос"}
    ]
    
    return {"voices": voices}

# ==================== ЗАПУСК ====================
if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8000))
    host = os.environ.get("HOST", "0.0.0.0")
    
    if not GROK_API_KEY:
        logger.warning("⚠️ GROK_API_KEY not set. Service will not function properly.")
    
    logger.info(f"Starting TTS Server with Grok on {host}:{port}")
    logger.info(f"Grok configured: {bool(GROK_API_KEY)}")
    
    uvicorn.run(
        app,
        host=host,
        port=port,
        log_level="info"
    )
