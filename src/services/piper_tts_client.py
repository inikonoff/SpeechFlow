import logging
import io
import wave
import asyncio
import subprocess
import aiohttp
from typing import Optional, List

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
            logger.info(f"🎵 Converting WAV ({len(wav_bytes)} bytes) to OGG...")
            
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
                logger.error(f"❌ FFmpeg conversion error: {stderr.decode()[:200]}")
                return None
            
            logger.info(f"✅ Converted WAV ({len(wav_bytes)} bytes) to OGG ({len(stdout)} bytes)")
            return stdout
            
        except FileNotFoundError:
            logger.error("❌ FFmpeg not found. Please install ffmpeg on the system")
            return None
        except Exception as e:
            logger.error(f"❌ Error converting to OGG: {e}", exc_info=True)
            return None
    
    async def _collect_wav_chunks(self, url: str, text: str) -> Optional[bytes]:
        """
        Собирает все WAV чанки из streaming endpoint и объединяет их
        
        Args:
            url: URL эндпоинта
            text: Текст для озвучивания
            
        Returns:
            bytes: Объединенный WAV файл или None в случае ошибки
        """
        try:
            session = await self._get_session()
            
            logger.info(f"📡 Sending request to {url}")
            logger.info(f"📝 Text: {text[:100]}...")
            
            async with session.post(
                url,
                json={"text": text, "voice": "amy"}
            ) as response:
                
                logger.info(f"📡 Response status: {response.status}")
                logger.info(f"📡 Response headers: {dict(response.headers)}")
                
                if response.status != 200:
                    error_text = await response.text()
                    logger.error(f"❌ Piper TTS error: HTTP {response.status} - {error_text}")
                    return None
                
                # Собираем все WAV чанки
                wav_chunks = []
                chunk_count = 0
                total_size = 0
                
                async for chunk in response.content.iter_chunked(8192):
                    if chunk:
                        wav_chunks.append(chunk)
                        chunk_count += 1
                        total_size += len(chunk)
                        logger.debug(f"📦 Received chunk {chunk_count}: {len(chunk)} bytes")
                
                logger.info(f"✅ Received {chunk_count} chunks, total {total_size} bytes")
                
                if not wav_chunks:
                    logger.error("❌ No audio data received from Piper")
                    return None
                
                # Если только один чанк, возвращаем как есть
                if len(wav_chunks) == 1:
                    logger.info(f"✅ Single chunk: {len(wav_chunks[0])} bytes")
                    return wav_chunks[0]
                
                # Объединяем все WAV чанки в один файл
                logger.info(f"🔄 Combining {len(wav_chunks)} WAV chunks...")
                combined_wav = await self._combine_wav_chunks(wav_chunks)
                
                if combined_wav:
                    logger.info(f"✅ Combined into single WAV: {len(combined_wav)} bytes")
                    return combined_wav
                else:
                    logger.error("❌ Failed to combine WAV chunks")
                    return None
                
        except asyncio.TimeoutError:
            logger.error("❌ Piper TTS request timed out")
            return None
        except aiohttp.ClientError as e:
            logger.error(f"❌ Piper TTS connection error: {e}")
            return None
        except Exception as e:
            logger.error(f"❌ Unexpected Piper TTS error: {e}", exc_info=True)
            return None
    
    async def _combine_wav_chunks(self, chunks: List[bytes]) -> Optional[bytes]:
        """
        Объединяет несколько WAV чанков в один файл
        
        Args:
            chunks: Список WAV чанков
            
        Returns:
            bytes: Объединенный WAV файл
        """
        if not chunks:
            return None
        
        if len(chunks) == 1:
            return chunks[0]
        
        try:
            # Читаем параметры из первого чанка
            first_chunk = io.BytesIO(chunks[0])
            with wave.open(first_chunk, 'rb') as first_wav:
                params = first_wav.getparams()
                logger.info(f"📊 First chunk params: {params}")
                
                # Создаем выходной WAV
                output = io.BytesIO()
                with wave.open(output, 'wb') as out_wav:
                    out_wav.setparams(params)
                    
                    # Записываем все чанки
                    for i, chunk in enumerate(chunks):
                        try:
                            chunk_io = io.BytesIO(chunk)
                            with wave.open(chunk_io, 'rb') as chunk_wav:
                                # Проверяем параметры
                                chunk_params = chunk_wav.getparams()
                                if (chunk_params.nchannels != params.nchannels or
                                    chunk_params.sampwidth != params.sampwidth or
                                    chunk_params.framerate != params.framerate):
                                    logger.warning(f"⚠️ Chunk {i} params mismatch, skipping")
                                    continue
                                
                                frames = chunk_wav.readframes(chunk_wav.getnframes())
                                out_wav.writeframes(frames)
                                logger.debug(f"✅ Added chunk {i}: {len(frames)} frames")
                        except Exception as e:
                            logger.warning(f"⚠️ Error processing chunk {i}: {e}")
                            continue
                
                output.seek(0)
                return output.getvalue()
                
        except Exception as e:
            logger.error(f"❌ Error combining WAV chunks: {e}", exc_info=True)
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
            logger.warning("⚠️ Empty text provided to TTS")
            return None
        
        logger.info(f"🎤 Piper TTS request for {len(text)} chars: {text[:100]}...")
        
        # Сначала получаем объединенный WAV из всех чанков
        wav_data = await self._collect_wav_chunks(f"{self.base_url}/tts/stream", text)
        
        if not wav_data:
            logger.error("❌ Failed to get WAV data from Piper")
            return None
        
        # Проверяем WAV заголовок
        if len(wav_data) > 44:
            logger.info(f"✅ WAV header OK: {wav_data[:4]} {wav_data[8:12]}")
            logger.info(f"✅ WAV size: {len(wav_data)} bytes")
        else:
            logger.error(f"❌ WAV too small: {len(wav_data)} bytes")
            return None
        
        # Конвертируем WAV в OGG
        ogg_data = await self._convert_wav_to_ogg(wav_data)
        
        if ogg_data:
            logger.info(f"✅ Final OGG size: {len(ogg_data)} bytes")
            return ogg_data
        else:
            logger.error("❌ Failed to convert WAV to OGG")
            return None
    
    async def health_check(self) -> bool:
        """Проверка доступности Piper сервиса"""
        try:
            session = await self._get_session()
            async with session.get(f"{self.base_url}/health") as response:
                if response.status == 200:
                    data = await response.json()
                    status = data.get("status") == "healthy" and data.get("model", {}).get("loaded", False)
                    logger.info(f"🏥 Piper health check: {status}")
                    return status
                logger.warning(f"🏥 Piper health check failed: {response.status}")
                return False
        except Exception as e:
            logger.error(f"🏥 Piper health check error: {e}")
            return False
    
    async def close(self):
        """Закрытие сессии"""
        if self.session and not self.session.closed:
            await self.session.close()
            logger.info("✅ PiperTTSClient session closed")
    
    async def __aenter__(self):
        return self
    
    async def __aexit__(self, exc_type, exc_val, exc_tb):
        await self.close()