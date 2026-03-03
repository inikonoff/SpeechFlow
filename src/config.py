from pydantic_settings import BaseSettings
from typing import List
import os


class Settings(BaseSettings):
    # Telegram
    TELEGRAM_BOT_TOKEN: str

    # Groq API Keys (строка с ключами через запятую)
    GROQ_API_KEYS: str = ""

    # Supabase
    SUPABASE_URL: str
    SUPABASE_KEY: str

    # Bot settings
    DEFAULT_USER_LEVEL: str = "intermediate"
    FREE_MESSAGES_LIMIT: int = 0
    VOICE_RESPONSE_MODE: str = "mirror"  # "always", "mirror", "never"
    TTS_VOICE: str = "austin"
    TEMP_DIR: str = "/tmp/speech_flow"
    CONTEXT_WINDOW: int = 5

    # PenFriend correction rate default (20=Relaxed, 50=Balanced, 80=Strict)
    CORRECTION_RATE_DEFAULT: int = 50

    class Config:
        env_file = ".env"
        extra = "ignore"

    @property
    def groq_api_keys_list(self) -> List[str]:
        if not self.GROQ_API_KEYS:
            return []
        return [k.strip() for k in self.GROQ_API_KEYS.split(",") if k.strip()]


settings = Settings()


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
