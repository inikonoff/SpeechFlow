from pydantic_settings import BaseSettings
from typing import List, Optional
import os


class Settings(BaseSettings):
    # Telegram
    TELEGRAM_BOT_TOKEN: str
    
    # Groq API Keys (строка с ключами через запятую)
    GROQ_API_KEYS: str = ""  # ✅ Должна быть строкой, не списком!
    
    # Supabase
    SUPABASE_URL: str
    SUPABASE_KEY: str
    
    # Bot settings
    DEFAULT_USER_LEVEL: str = "intermediate"
    FREE_MESSAGES_LIMIT: int = 0
    VOICE_RESPONSE_MODE: str = "mirror"  # "always", "mirror", "never"
    TTS_VOICE: str = "autumn"  # Groq Orpheus: autumn, diana, hannah, austin, daniel, troy
    
    # TTS Provider settings
    TTS_PROVIDER: str = "groq"  # "groq" или "piper"
    PIPER_TTS_URL: str = ""  # URL Piper TTS сервиса (если используется)
    
    class Config:
        env_file = ".env"
        extra = "ignore"
    
    @property
    def groq_api_keys_list(self) -> List[str]:
        """Преобразует строку с ключами в список"""
        if not self.GROQ_API_KEYS:
            return []
        # Разбиваем по запятой, убираем пробелы, удаляем пустые
        return [k.strip() for k in self.GROQ_API_KEYS.split(",") if k.strip()]


settings = Settings()


# ADMIN_IDS отдельно
def get_admin_ids() -> List[int]:
    admin_ids_str = os.environ.get("ADMIN_IDS", "")
    if not admin_ids_str:
        return []
    
    ids = []
    for id_str in admin_ids_str.split(","):
        id_str = id_str.strip()
        if id_str and id_str.isdigit():
            ids.append(int(id_str))
    return ids


ADMIN_IDS = get_admin_ids()
