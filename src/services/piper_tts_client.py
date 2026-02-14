import logging
import io
import wave
import subprocess
import asyncio
import aiohttp
from typing import Optional

logger = logging.getLogger(__name__)


class PiperTTSClient:
    """Клиент для взаимодействия с оптимизированным Piper TTS сервисом"""
    
    def __init__(self, base_url: str, timeout: int = 30):
        """
        Args:
            base_url: URL Piper TTS сервиса (например, http://localhost:8000)
            timeout: Таймаут запроса в секундах
        """
        self.base_url = base_url.rstrip('/')
        self.timeout = timeout
        self.session: Optional[aiohttp.ClientSession] = None
        logger.info(f"✅ PiperTTSClient initialized with URL: {self.base_url}")
    
    async def _get_session(self) -> aiohttp.ClientSession:
        """Получение или создание сессии"""
        if self.session is None or self.session.closed:
            self.session = aiohttp.ClientSession(
                timeout=aiohttp.ClientTimeout(total=self.timeout)
            )
        return self.session
    
    async def _convert_wav_to_ogg(self, wav_bytes: bytes) -> Optional[bytes]:
        """
        Конвертирует WAV в OGG Opus с помощью ffmpeg
        
        Args:
            wav_bytes: Байты WAV файла
            
        Returns:
            bytes: OGG Opus файл или None в случае ошибки
        """
        try:
            # Запускаем ffmpeg для конвертации
            process = await asyncio.create_subprocess_exec(
                'ffmpeg',
                '-i', 'pipe:0',           # Вход из stdin
                '-c:a', 'libopus',          # Кодек Opus
                '-b:a', '32k',              # Битрейт 32 kbps (стандарт для Telegram)
                '-ar', '24000',              # Частота 24 кГц
                '-application', 'voip',      # Оптимизация для речи
                '-frame_duration', '60',     # Длительность фрейма
                '-packet_loss', '1',          # Устойчивость к потерям
                '-f', 'ogg',                  # Выходной формат OGG
                'pipe:1',                     # Выход в stdout
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE
            )
            
            # Отправляем WAV в stdin и получаем результат
            stdout, stderr = await process.communicate(input=wav_bytes)
            
            if process.returncode != 0:
                logger.error(f"FFmpeg conversion error: {stderr.decode()}")
                return None
            
            logger.info(f"✅ Converted WAV ({len(wav_bytes)} bytes) to OGG ({len(stdout)} bytes)")
            return stdout
            
        except FileNotFoundError:
            logger.error("❌ FFmpeg not found. Please install ffmpeg on the system")
            # Fallback: возвращаем как есть (Telegram не примет, но хотя бы не упадем)
            return wav_bytes
        except Exception as e:
            logger.error(f"❌ Error converting to OGG: {e}")
            return None
    
    async def text_to_speech(self, text: str) -> Optional[bytes]:
        """
        Генерация речи из текста через Piper TTS сервис и конвертация в OGG
        
        Args:
            text: Текст для озвучивания
            
        Returns:
            bytes: Аудио в формате OGG Opus или None в случае ошибки
        """
        if not text or not text.strip():
            logger.warning("Empty text provided to TTS")
            return None
        
        try:
            session = await self._get_session()
            
            # Используем streaming endpoint
            async with session.post(
                f"{self.base_url}/tts/stream",
                json={"text": text, "voice": "amy"}
            ) as response:
                
                if response.status != 200:
                    logger.error(f"Piper TTS error: HTTP {response.status}")
                    return None
                
                # Собираем все чанки WAV
                wav_chunks = []
                async for chunk in response.content.iter_chunked(8192):
                    if chunk:
                        wav_chunks.append(chunk)
                
                if not wav_chunks:
                    logger.error("No audio data received from Piper")
                    return None
                
                # Объединяем чанки в один WAV файл
                wav_data = b''.join(wav_chunks)
                logger.info(f"✅ Received WAV from Piper: {len(wav_data)} bytes")
                
                # Конвертируем WAV в OGG
                ogg_data = await self._convert_wav_to_ogg(wav_data)
                
                if ogg_data:
                    return ogg_data
                else:
                    # Если конвертация не удалась, логируем и возвращаем None
                    logger.error("Failed to convert WAV to OGG")
                    return None
                
        except asyncio.TimeoutError:
            logger.error("Piper TTS request timed out")
            return None
        except aiohttp.ClientError as e:
            logger.error(f"Piper TTS connection error: {e}")
            return None
        except Exception as e:
            logger.error(f"Unexpected Piper TTS error: {e}")
            return None
    
    async def health_check(self) -> bool:
        """Проверка доступности Piper сервиса"""
        try:
            session = await self._get_session()
            async with session.get(f"{self.base_url}/health") as response:
                if response.status == 200:
                    data = await response.json()
                    return data.get("status") == "healthy" and data.get("model_loaded", False)
                return False
        except Exception as e:
            logger.error(f"Piper health check failed: {e}")
            return False
    
    async def close(self):
        """Закрытие сессии"""
        if self.session and not self.session.closed:
            await self.session.close()
            logger.info("PiperTTSClient session closed")
    
    async def __aenter__(self):
        return self
    
    async def __aexit__(self, exc_type, exc_val, exc_tb):
        await self.close()
