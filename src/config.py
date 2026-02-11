# src/config.py
from pydantic_settings import BaseSettings
from typing import List


class Settings(BaseSettings):
    # Telegram
    TELEGRAM_BOT_TOKEN: str
    
    # Groq API Keys (через запятую)
    GROQ_API_KEYS: List[str] = []
    
    # Supabase
    SUPABASE_URL: str
    SUPABASE_KEY: str
    
    # Admin IDs
    ADMIN_IDS: List[int] = []
    
    # Bot settings
    DEFAULT_USER_LEVEL: str = "intermediate"
    
    class Config:
        env_file = ".env"
    
    @property
    def groq_api_keys_list(self) -> List[str]:
        """Преобразуем строку из .env в список"""
        if isinstance(self.GROQ_API_KEYS, str):
            return [k.strip() for k in self.GROQ_API_KEYS.split(",") if k.strip()]
        return self.GROQ_API_KEYS


settings = Settings()
