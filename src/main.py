import os
import sys
import signal
import logging
import asyncio
from contextlib import asynccontextmanager
from datetime import datetime
from fastapi import FastAPI
from aiogram import Bot, Dispatcher
from aiogram.enums import ParseMode
from aiogram.client.default import DefaultBotProperties
from src.config import settings, ADMIN_IDS
from src.bot.handlers import start, level, menu, message
from src.bot.middlewares.user_middleware import UserMiddleware
from src.services.groq_client import groq_client
from src.services.supabase_db import db

# Настройка логирования
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    stream=sys.stdout,
    force=True
)
logger = logging.getLogger(__name__)

# Глобальные переменные
bot = Bot(
    token=settings.TELEGRAM_BOT_TOKEN,
    default=DefaultBotProperties(parse_mode=ParseMode.MARKDOWN)
)
dp = Dispatcher()
shutdown_event = asyncio.Event()


# ============================================================================
# ОБРАБОТКА СИГНАЛОВ (SIGTERM)
# ============================================================================

def handle_sigterm(signum, frame):
    """Обработчик сигнала SIGTERM от Render"""
    logger.info("📡 Received SIGTERM signal, initiating graceful shutdown...")
    asyncio.create_task(trigger_shutdown())


async def trigger_shutdown():
    """Триггер для graceful shutdown"""
    shutdown_event.set()


# ============================================================================
# LIFESPAN
# ============================================================================

@asynccontextmanager
async def lifespan(app: FastAPI):
    """Lifespan для FastAPI"""
    logger.info("🚀 Starting Speech Flow AI Bot...")
    
    # Регистрируем обработчик SIGTERM
    signal.signal(signal.SIGTERM, handle_sigterm)
    logger.info("✅ SIGTERM handler registered")
    
    # Запускаем бота
    await startup()
    
    yield  # Здесь работает приложение
    
    # Ждём сигнала завершения или graceful shutdown
    logger.info("🛑 Shutting down Speech Flow AI Bot...")
    await shutdown()


app = FastAPI(
    lifespan=lifespan,
    title="Speech Flow AI Bot",
    version="1.0.0"
)


# =============================================================================
# ENDPOINTS ДЛЯ UPTIMEROBOT
# =============================================================================

@app.get("/")
async def root():
    """Корневой эндпоинт"""
    return {
        "status": "alive", 
        "service": "Speech Flow AI",
        "version": "1.0.0",
        "message": "Bot is running!",
        "timestamp": datetime.utcnow().isoformat()
    }


@app.get("/health")
async def health_check():
    """Health check endpoint для Render/UptimeRobot"""
    return {
        "status": "healthy", 
        "service": "speech-flow-bot",
        "uptime": True,
        "timestamp": datetime.utcnow().isoformat()
    }


@app.get("/ping")
async def ping():
    """Простой ping endpoint"""
    return {"pong": True, "timestamp": datetime.utcnow().isoformat()}


@app.get("/status")
async def status():
    """Детальный статус бота"""
    try:
        bot_info = await bot.get_me()
        return {
            "status": "running",
            "bot": {
                "username": bot_info.username,
                "id": bot_info.id,
                "name": bot_info.first_name
            },
            "groq_clients": len(groq_client.clients),
            "admin_count": len(ADMIN_IDS),
            "timestamp": datetime.utcnow().isoformat()
        }
    except Exception as e:
        logger.error(f"Status check error: {e}")
        return {
            "status": "error",
            "error": str(e),
            "timestamp": datetime.utcnow().isoformat()
        }


# =============================================================================
# STARTUP/SHUTDOWN
# =============================================================================

async def startup():
    """Запуск бота"""
    try:
        # Регистрируем middleware
        dp.update.middleware(UserMiddleware())
        
        # Регистрируем роутеры
        dp.include_router(start.router)
        dp.include_router(level.router)
        dp.include_router(menu.router)
        dp.include_router(message.router)
        
        # Удаляем вебхук
        await bot.delete_webhook(drop_pending_updates=True)
        
        # Запускаем polling в фоне
        asyncio.create_task(run_polling())
        
        logger.info("✅ Bot started successfully!")
        logger.info(f"👤 Admin IDs: {ADMIN_IDS}")
        logger.info(f"🔑 Groq clients: {len(groq_client.clients)}")
        
    except Exception as e:
        logger.error(f"❌ Startup error: {e}")
        raise


async def run_polling():
    """Запуск polling с обработкой завершения"""
    try:
        await dp.start_polling(bot)
    except asyncio.CancelledError:
        logger.info("Polling task cancelled")
    except Exception as e:
        logger.error(f"Polling error: {e}")
    finally:
        logger.info("Polling stopped")


async def shutdown():
    """Остановка бота"""
    try:
        # Даём время на завершение задач
        logger.info("⏳ Waiting for ongoing tasks to complete (up to 30 seconds)...")
        await asyncio.sleep(30)
        
        # Закрываем сессию бота
        await bot.session.close()
        logger.info("✅ Bot session closed")
    except Exception as e:
        logger.error(f"❌ Shutdown error: {e}")


# =============================================================================
# MAIN
# =============================================================================

if __name__ == "__main__":
    import uvicorn
    
    # Берём порт из переменной окружения
    port = int(os.environ.get("PORT", 8000))
    
    logger.info("=" * 50)
    logger.info(f"📡 Starting in local mode...")
    logger.info(f"📌 PORT from env: {os.environ.get('PORT', 'not set')}")
    logger.info(f"🔌 Binding to port: {port}")
    logger.info("=" * 50)
    
    uvicorn.run(app, host="0.0.0.0", port=port)
