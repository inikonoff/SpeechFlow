import os
import logging
import tempfile
from pathlib import Path
from typing import Optional

import aiofiles

logger = logging.getLogger(__name__)


async def save_voice_file(file_bytes: bytes, file_extension: str = "ogg") -> Path:
    """
    Сохраняет голосовое сообщение во временный файл.
    Groq принимает OGG напрямую, конвертация не нужна!
    """
    try:
        # Создаем временный файл с правильным расширением
        with tempfile.NamedTemporaryFile(suffix=f".{file_extension}", delete=False) as tmp:
            tmp_path = Path(tmp.name)
        
        # Асинхронно записываем байты
        async with aiofiles.open(tmp_path, 'wb') as f:
            await f.write(file_bytes)
        
        logger.debug(f"Saved voice file: {tmp_path}")
        return tmp_path
        
    except Exception as e:
        logger.error(f"Error saving voice file: {e}")
        raise


async def cleanup_file(file_path: Path) -> None:
    """Удаляет временный файл"""
    try:
        if file_path and file_path.exists():
            file_path.unlink()
            logger.debug(f"Cleaned up file: {file_path}")
    except Exception as e:
        logger.error(f"Error cleaning up file {file_path}: {e}")


async def read_file_bytes(file_path: Path) -> bytes:
    """Читает файл и возвращает байты"""
    try:
        async with aiofiles.open(file_path, 'rb') as f:
            return await f.read()
    except Exception as e:
        logger.error(f"Error reading file {file_path}: {e}")
        raise
