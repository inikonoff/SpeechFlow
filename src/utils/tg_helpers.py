"""
Helpers for Telegram message editing that silently ignore
'message is not modified' errors — the most common false-positive
when a user taps the same button twice.
"""
import logging
from typing import Any, Optional

logger = logging.getLogger(__name__)

_NOT_MODIFIED = "message is not modified"


async def safe_edit_text(message, text: str, **kwargs) -> Any:
    try:
        return await message.edit_text(text, **kwargs)
    except Exception as e:
        if _NOT_MODIFIED in str(e):
            return message
        raise


async def safe_edit_reply_markup(message, **kwargs) -> Any:
    try:
        return await message.edit_reply_markup(**kwargs)
    except Exception as e:
        if _NOT_MODIFIED in str(e):
            return message
        raise


async def safe_edit_caption(message, **kwargs) -> Any:
    try:
        return await message.edit_caption(**kwargs)
    except Exception as e:
        if _NOT_MODIFIED in str(e):
            return message
        raise
