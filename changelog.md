Пока твой бот деплоится, давай разберем теорию, а затем я выдам тебе все запрошенные артефакты!

### 🧠 Что такое FastAPI и Prometheus? (Простыми словами)

**FastAPI** — это суперсовременный и очень быстрый фреймворк для создания веб-серверов на Python. 
* *Зачем он боту?* Телеграм-боту (который работает через Polling) по сути веб-сервер не нужен, он сам "стучится" в Telegram. **Но** облачные хостинги (Render, Heroku и т.д.) созданы для веб-сайтов. Если твой код не открывает веб-порт (PORT) и не отвечает на HTTP-запросы, хостинг думает, что программа зависла, и "убивает" её. 
FastAPI здесь работает как "фасад" или "ресепшн". Он открывает порт, принимает пинги от хостинга и UptimeRobot, раздает метрики, а сам бот тихо работает на заднем фоне.

**Prometheus и эндпоинт `/metrics`** — это индустриальный стандарт мониторинга.
* Представь, что ты хочешь красивую панель (дашборд) с графиками: сколько оперативы ест бот, сколько запросов в секунду, сколько ошибок. Для этого программисты используют связку **Prometheus + Grafana**.
* Prometheus — это программа, которая раз в минуту заходит на твой адрес `твойсайт.com/metrics`, скачивает оттуда текстовый файл с циферками (потребление CPU, аптайм) и складывает в свою базу. А Grafana рисует из этого красивые графики. 
* То, что у тебя в коде есть этот эндпоинт, делает твоего бота **Enterprise-ready** (готовым к серьезным нагрузкам в больших компаниях).

---

### 📦 АРТЕФАКТ 1: Универсальный шаблон бота (Фреймворк "Анти-падение")

Сохрани этот код себе как `template_main.py`. Это идеальная обертка для **любого** будущего бота на Aiogram 3. В ней уже есть: неубиваемый Polling, грациозное выключение (Graceful Shutdown) при деплое, метрики и FastAPI "ресепшн". Тебе нужно только подставить свои роутеры.

```python
import os
import sys
import signal
import logging
import asyncio
import time
import psutil
from datetime import timedelta, datetime
from contextlib import asynccontextmanager
from fastapi import FastAPI, Response, Request
from aiogram import Bot, Dispatcher
from aiogram.enums import ParseMode
from aiogram.client.default import DefaultBotProperties

# === ТВОИ ИМПОРТЫ ===
# from src.config import settings
# from src.handlers import my_router

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s", stream=sys.stdout)
logger = logging.getLogger(__name__)

# === НАСТРОЙКИ БОТА ===
TOKEN = os.getenv("BOT_TOKEN", "YOUR_TOKEN_HERE")
bot = Bot(token=TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
dp = Dispatcher()

# === ГЛОБАЛЬНЫЕ ПЕРЕМЕННЫЕ ===
start_time = time.time()
polling_task = None
is_shutting_down = False
shutdown_event = asyncio.Event()
stats = {"total_requests": 0, "errors": 0}

# === GRACEFUL SHUTDOWN (Мягкая остановка для Render/Heroku) ===
def handle_sigterm(signum, frame):
    global is_shutting_down
    if is_shutting_down: return
    logger.info("📡 Received SIGTERM! Graceful shutdown initiated...")
    is_shutting_down = True
    loop = asyncio.get_running_loop()
    loop.call_soon_threadsafe(lambda: asyncio.create_task(shutdown_event.set()))

# === POLLING TASK ===
async def run_polling():
    global is_shutting_down
    while not is_shutting_down:
        try:
            logger.info("🚀 Starting bot polling...")
            await dp.start_polling(bot)
        except asyncio.CancelledError:
            break
        except Exception as e:
            if is_shutting_down: break
            logger.error(f"❌ Polling crashed: {e}. Restarting in 5s...")
            await asyncio.sleep(5)

# === ЖИЗНЕННЫЙ ЦИКЛ ПРИЛОЖЕНИЯ ===
@asynccontextmanager
async def lifespan(app: FastAPI):
    global polling_task
    logger.info("🟢 App starting...")
    
    # Регистрация роутеров
    # dp.include_router(my_router)
    
    await bot.delete_webhook(drop_pending_updates=True)
    polling_task = asyncio.create_task(run_polling())
    
    # Перехват сигнала завершения от хостинга
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(sig, handle_sigterm, sig, None)
    
    yield # Сервер работает
    
    logger.info("🔴 App shutting down...")
    if polling_task and not polling_task.done():
        polling_task.cancel()
    await bot.session.close()

app = FastAPI(lifespan=lifespan, docs_url=None, redoc_url=None)

# === MIDDLEWARE ДЛЯ МЕТРИК ===
@app.middleware("http")
async def monitor_requests(request: Request, call_next):
    stats["total_requests"] += 1
    try:
        return await call_next(request)
    except Exception:
        stats["errors"] += 1
        raise

# === ENDPOINTS ДЛЯ ХОСТИНГА (UptimeRobot, Render) ===
@app.get("/health")
@app.head("/health")
async def health():
    return Response(content="OK", status_code=200)

@app.get("/metrics")
async def metrics():
    uptime = int(time.time() - start_time)
    ram_mb = psutil.Process().memory_info().rss / 1024 / 1024
    cpu = psutil.Process().cpu_percent()
    
    text = f"""# HELP bot_uptime Uptime in seconds
# TYPE bot_uptime gauge
bot_uptime {uptime}
# HELP bot_ram_mb RAM usage
bot_ram_mb {ram_mb:.2f}
# HELP bot_cpu CPU usage
bot_cpu {cpu}
# HELP bot_requests Total requests
bot_requests {stats["total_requests"]}
"""
    return Response(content=text, media_type="text/plain")

if __name__ == "__main__":
    import uvicorn
    port = int(os.environ.get("PORT", 8080))
    uvicorn.run("template_main:app", host="0.0.0.0", port=port, log_level="info", workers=1)
```

---

### 📝 АРТЕФАКТ 2: Changelog (Журнал изменений)

**Дата:** Февраль 2025  
**Проект:** SpeechFlow AI Bot

#### ✨ Добавлено (Added):
* **Системное меню Telegram (Commands Menu):** Добавлена синяя кнопка меню слева от поля ввода с командами `/start`, `/voice`, `/stats`, `/vocabulary`, `/author`.
* **Выбор голоса (Voice Selection):** Внедрена команда `/voice` и логика сохранения предпочитаемого голоса ИИ (из пула Groq Orpheus) в Supabase для каждого пользователя.
* **Supabase Anti-Sleep Mechanism:** Добавлена фоновая асинхронная задача `db_keep_alive()`, которая раз в 12 часов пингует базу данных, чтобы предотвратить ее удаление на бесплатном тарифе (Free Tier).
* **Сохранение аналитики диалогов:** Бот теперь реально записывает новые английские слова и грамматические ошибки в базу данных в процессе диалога.

#### 🔧 Изменено (Changed):
* **Переход с Markdown на HTML:** Полностью переписана логика форматирования текста в хендлерах (статистика, словарь, ответы ИИ). Это устранило критические падения бота из-за спецсимволов при генерации текста нейросетью.
* **Промпты для ИИ:** Жестко зафиксировано условие "100% in Russian" для блока `explanation` во всех уровнях (A1-C2), чтобы бот не смешивал языки при объяснении грамматических правил.

#### 🐛 Исправлено (Fixed):
* **Баг со статистикой:** Исправлен алгоритм подсчета ошибок в `get_user_stats()` (заменен сломанный метод Supabase `count="exact"` на корректную группировку словаря на стороне Python).
* **Баг фейковых коллбеков:** Убран опасный антипаттерн `FakeCallback` из `message.py`, крашивший бота при вызове статистики через команду.

---

### 📖 АРТЕФАКТ 3: Новый `README.md`

```markdown
# 🗣 SpeechFlow AI Bot

An advanced AI-powered English conversational tutor built for Telegram. SpeechFlow acts as a charismatic speaking partner, analyzing voice and text inputs in real-time, correcting grammar, building custom vocabularies, and replying with natural synthetic voice.

## 🌟 Features

*   **Real-time STT & TTS:** Uses Groq Whisper for lightning-fast speech-to-text and Orpheus/OpenAI for natural text-to-speech.
*   **Adaptive LLM Pedagogy:** Powered by `llama3-70b-8192`. The AI adapts its vocabulary, sentence length, and grammar complexity based on the user's level (Beginner to Advanced).
*   **Surgical Corrections:** The bot doesn't just chat; it catches mistakes, explains *why* they are wrong (in Russian), and provides correct alternatives.
*   **Smart Vocabulary Builder:** Automatically extracts new idioms and words from the conversation and saves them to your personal Supabase database.
*   **Gamification & Stats:** Tracks your daily streak, message counts, and categorizes your most frequent grammar/vocabulary mistakes.
*   **Customizable AI Voice:** Choose your preferred AI tutor's voice directly from the Telegram menu (`/voice`).

## 🛠 Tech Stack

*   **Framework:** Python 3.11+, Aiogram 3.x, FastAPI
*   **AI Engine:** Groq API (Whisper + LLaMA3)
*   **Database:** Supabase (PostgreSQL)
*   **Deployment:** Ready for Render / Heroku
*   **Monitoring:** Built-in Uptime endpoints and Prometheus `/metrics` exporter.

## 🚀 Getting Started

### 1. Environment Variables (`.env`)
Create a `.env` file in the root directory:
```env
TELEGRAM_BOT_TOKEN=your_telegram_bot_token
GROQ_API_KEYS=key1,key2,key3 # Supports multiple keys for round-robin balancing
SUPABASE_URL=your_supabase_url
SUPABASE_KEY=your_supabase_anon_key
ADMIN_IDS=123456789,987654321
DEFAULT_USER_LEVEL=intermediate
FREE_MESSAGES_LIMIT=0
VOICE_RESPONSE_MODE=mirror # "always", "mirror", or "never"
TTS_VOICE=austin
```

### 2. Database Setup (Supabase)
Ensure your Supabase project has the following tables:
*   `users` (id, telegram_id, username, level, streak_days, total_tokens_used, free_messages_used, voice, created_at, last_active)
*   `vocabulary` (id, user_id, word_or_phrase, translation, context_sentence, mastery_score, created_at)
*   `error_logs` (id, user_id, category, mistake_text, created_at)

### 3. Running Locally
```bash
pip install -r requirements.txt
python -m src.main
```

## 📊 Monitoring & Infrastructure
SpeechFlow is designed to be highly available on free cloud tiers. It includes a FastAPI wrapper that serves the following endpoints:

*   **`/health`**: Keep-alive endpoint for UptimeRobot (supports GET/HEAD).
*   **`/metrics`**: Exposes Prometheus-compatible metrics (CPU, RAM, requests, uptime).
*   **`/status`**: Detailed JSON dashboard of bot health and connected Groq clients.
*   **Database Keep-Alive:** A background task pings Supabase every 12 hours to prevent database pausing on free tiers.

## 👨‍💻 Author
Created by [@inikonoff](https://t.me/inikonoff). Feedback and contributions are welcome!
```
