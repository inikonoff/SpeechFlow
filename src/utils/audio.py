import asyncio
import logging
import os
import tempfile
from pathlib import Path
from typing import Optional

import aiofiles
from pydub import AudioSegment

logger = logging.getLogger(__name__)


async def convert_ogg_to_mp3(input_path: Path, output_path: Optional[Path] = None) -> Path:
    """
    Конвертирует OGG в MP3 для Whisper.
    Возвращает путь к MP3 файлу.
    """
    try:
        if output_path is None:
            output_path = input_path.with_suffix('.mp3')
        
        # Загружаем аудио
        audio = AudioSegment.from_ogg(str(input_path))
        
        # Экспортируем в MP3
        audio.export(str(output_path), format="mp3", bitrate="128k")
        
        return output_path
        
    except Exception as e:
        logger.error(f"Error converting audio: {e}")
        raise


async def save_voice_file(file_bytes: bytes, file_extension: str = "ogg") -> Path:
    """
    Сохраняет голосовое сообщение во временный файл.
    """
    try:
        # Создаем временный файл
        with tempfile.NamedTemporaryFile(suffix=f".{file_extension}", delete=False) as tmp:
            tmp_path = Path(tmp.name)
        
        # Асинхронно записываем байты
        async with aiofiles.open(tmp_path, 'wb') as f:
            await f.write(file_bytes)
        
        return tmp_path
        
    except Exception as e:
        logger.error(f"Error saving voice file: {e}")
        raise


async def cleanup_file(file_path: Path) -> None:
    """Удаляет временный файл"""
    try:
        if file_path.exists():
            file_path.unlink()
    except Exception as e:
        logger.error(f"Error cleaning up file {file_path}: {e}")
