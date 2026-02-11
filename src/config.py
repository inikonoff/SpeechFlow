from pydantic_settings import BaseSettings
from typing import List, Optional
import os


class Settings(BaseSettings):
    # Telegram
    TELEGRAM_BOT_TOKEN: str
    
    # Groq API Keys (через запятую)
    GROQ_API_KEYS: List[str] = []
    
    # Supabase
    SUPABASE_URL: str
    SUPABASE_KEY: str
    
    # Bot settings
    DEFAULT_USER_LEVEL: str = "intermediate"
    FREE_MESSAGES_LIMIT: int = 0
    
    class Config:
        env_file = ".env"
        extra = "ignore"


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
