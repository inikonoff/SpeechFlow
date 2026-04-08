from pydantic_settings import BaseSettings
from typing import List, Optional
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

SUBSCRIPTION_PLANS = ("standard", "plus", "pro")

DAILY_VOICE_LIMITS: dict = {
    "standard": 5,
    "plus": 10,
    "pro": 20,
}

def get_daily_voice_limit(subscription_plan: str) -> int:
    return DAILY_VOICE_LIMITS.get(subscription_plan, DAILY_VOICE_LIMITS["standard"])


# ─── Онбординг: file_id голосовых Mrs. Smith ──────────────────────────────────
# Заполнить после загрузки WAV-файлов в Telegram через временный хендлер.
# Инструкция: отправь боту файл как документ → хендлер ответит file_id.

ONBOARDING_VOICE_START        = "AwACAgIAAxkBAAIL_WnRYDDM5b9rybHu-_As7OJ_dgz5AAI1mwACvTiISiGuN-HDFKe1OwQ"  # voice_start.wav
ONBOARDING_VOICE_BEGINNER     = "AwACAgIAAxkBAAIL-2nRYBMO7MUfY3ebVI_KckDupstuAAIxmwACvTiISo0f4nsI0jY7OwQ"  # voice_beginner.wav
ONBOARDING_VOICE_INTERMEDIATE = "AwACAgIAAxkBAAINT2nWpa_jzRkuIhy-XbFQu86EHh3DAAL6jAACl5C5SntI2Q1DkPrOOwQ"  # voice_intermediate.wav
ONBOARDING_VOICE_ADVANCED     = "AwACAgIAAxkBAAINUWnWpdH4geqBwwbjClenyj0i3AENAAL7jAACl5C5SqDXQOIFrPZ8OwQ"  # voice_advanced.wav

# Тексты для спойлеров под каждым голосовым (оригинал + перевод)
ONBOARDING_SPOILERS = {
    "start": {
        "en": (
            "Hello. I'm Mrs. Smith — and I'm very glad you're here. "
            "This isn't a course, and I won't be giving you homework. "
            "We're simply going to talk — in English, at your pace. "
            "But first, tell me: how would you describe your English right now? "
            "Choose the option that feels closest."
        ),
        "ru": (
            "Привет. Я миссис Смит — и я очень рада, что вы здесь. "
            "Это не урок, и я не буду давать домашние задания. "
            "Мы просто будем разговаривать - по-английски и в вашем темпе. "
            "Но сначала скажите: как бы вы описали свой английский прямо сейчас? "
            "Выберите вариант, который кажется наиболее близким."
        ),
    },
    "beginner": {
        "en": (
            "Beginner — that's an honest answer, and I appreciate that. "
            "We'll keep things simple and comfortable. "
            "The most important thing right now is that you speak. "
            "Don't worry about mistakes — that's what I'm here for. "
            "Go ahead, say hello. Tell me something small about your day."
        ),
        "ru": (
            "Начинающий — это честный ответ, и я это ценю. "
            "Мы будем держаться простых и комфортных тем. "
            "Самое важное сейчас — чтобы вы говорили. "
            "Не беспокойтесь об ошибках — для этого я здесь. "
            "Вперёд, поздоровайтесь. Расскажите что-нибудь о своём дне."
        ),
    },
    "intermediate": {
        "en": (
            "Intermediate — good. You know more than you think you do. "
            "What we're going to work on is getting that knowledge out of your head "
            "and into your speech, naturally. "
            "So let's start right now. Tell me — what's been on your mind lately?"
        ),
        "ru": (
            "Средний уровень — хорошо. Вы знаете больше, чем вам кажется. "
            "Мы будем работать над тем, чтобы эти знания выходили из головы "
            "и становились живой речью — естественно. "
            "Давайте начнём прямо сейчас. Скажите — о чём вы думали в последнее время?"
        ),
    },
    "advanced": {
        "en": (
            "Advanced. Then we won't waste time on basics. "
            "I'm curious about you — how you think, what you care about, "
            "what you find difficult to express in English. "
            "That's where the real work is. So — tell me something that matters to you."
        ),
        "ru": (
            "Продвинутый. Тогда не будем тратить время на основы. "
            "Мне интересны вы — как вы думаете, что вам важно, "
            "что вам трудно выразить по-английски. "
            "Вот где ведётся настоящая работа. Итак — расскажите мне о чём-то важном для вас."
        ),
    },
}
