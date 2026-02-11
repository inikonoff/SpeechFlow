from pydantic_settings import BaseSettings
from typing import List, Optional


class Settings(BaseSettings):
    # Telegram
    TELEGRAM_BOT_TOKEN: str
    
    # Groq API Keys (через запятую)
    GROQ_API_KEYS: List[str] = []
    
    # Supabase
    SUPABASE_URL: str
    SUPABASE_KEY: str
    
    # Admin IDs - ПРАВИЛЬНАЯ ОБРАБОТКА
    ADMIN_IDS: List[int] = []
    
    # Bot settings
    DEFAULT_USER_LEVEL: str = "intermediate"
    
    class Config:
        env_file = ".env"
        env_file_encoding = "utf-8"
        # Разрешаем автоматическую конвертацию из строки в список
        coerce_numbers_to_str = True
    
    @classmethod
    def parse_env_var(cls, field_name: str, raw_val: str):
        if field_name == "ADMIN_IDS":
            # Если это строка с запятыми - разбиваем в список
            if isinstance(raw_val, str) and "," in raw_val:
                return [int(id.strip()) for id in raw_val.split(",") if id.strip()]
            # Если это одно число - делаем список с одним элементом
            elif isinstance(raw_val, str) and raw_val.strip().isdigit():
                return [int(raw_val.strip())]
            # Если это уже список (например, из .env как строка)
            elif isinstance(raw_val, list):
                return raw_val
        return super().parse_env_var(field_name, raw_val)


settings = Settings()
