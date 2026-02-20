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
    
    # TTS Provider settings - читается из .env
    TTS_PROVIDER: Optional[str] = None  # "groq" или "piper" - ОБЯЗАТЕЛЬНО указать в .env!
    PIPER_TTS_URL: Optional[str] = None  # URL Piper TTS сервиса (обязательно для piper)
    
    def __init__(self, **data):
        super().__init__(**data)
        
        # Валидация TTS настроек
        if not self.TTS_PROVIDER:
            raise ValueError(
                "⚠️ TTS_PROVIDER не указан в .env!\n"
                "Добавьте в .env одну из строк:\n"
                "  TTS_PROVIDER=groq  (платный, лучшее качество)\n"
                "  TTS_PROVIDER=piper (бесплатный, нужен PIPER_TTS_URL)"
            )
        
        if self.TTS_PROVIDER not in ["groq", "piper"]:
            raise ValueError(
                f"⚠️ Неверное значение TTS_PROVIDER: {self.TTS_PROVIDER}\n"
                f"Доступные значения: 'groq' или 'piper'"
            )
        
        if self.TTS_PROVIDER == "piper" and not self.PIPER_TTS_URL:
            raise ValueError(
                "⚠️ Для TTS_PROVIDER=piper нужно указать PIPER_TTS_URL в .env!\n"
                "Например: PIPER_TTS_URL=https://piper-tts-service.onrender.com"
            )
    
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
