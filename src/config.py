from pydantic_settings import BaseSettings
from typing import List


class Settings(BaseSettings):
    # Telegram
    TELEGRAM_BOT_TOKEN: str
    
    # Groq
    GROQ_API_KEY: str
    
    # Supabase
    SUPABASE_URL: str
    SUPABASE_KEY: str
    
    # Admin IDs (через запятую: "123456789,987654321")
    ADMIN_IDS: List[int] = []
    
    # Bot settings
    FREE_MESSAGES_LIMIT: int = 0  # 0 = без ограничений
    DEFAULT_USER_LEVEL: str = "intermediate"
    
    class Config:
        env_file = ".env"


settings = Settings()
