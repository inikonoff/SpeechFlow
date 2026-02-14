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
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger(__name__)

# Создаем FastAPI приложение
@asynccontextmanager
async def lifespan(app: FastAPI):
    """Lifespan для FastAPI"""
    logger.info("🚀 Starting Speech Flow AI Bot...")
    await startup()
    yield
    logger.info("🛑 Shutting down Speech Flow AI Bot...")
    await shutdown()

app = FastAPI(
    lifespan=lifespan,
    title="Speech Flow AI Bot",
    version="1.0.0"
)

bot = Bot(
    token=settings.TELEGRAM_BOT_TOKEN,
    default=DefaultBotProperties(parse_mode=ParseMode.MARKDOWN)
)
dp = Dispatcher()

# =============================================================================
# ENDPOINTS ДЛЯ UPTIMEROBOT И МОНИТОРИНГА
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
    """
    Health check endpoint для Render/UptimeRobot
    Возвращает простой статус для проверки работоспособности
    """
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
        
        # Запускаем polling
        asyncio.create_task(dp.start_polling(bot))
        
        logger.info("✅ Bot started successfully!")
        logger.info(f"👤 Admin IDs: {ADMIN_IDS}")
        logger.info(f"🔑 Groq clients: {len(groq_client.clients)}")
        
    except Exception as e:
        logger.error(f"❌ Startup error: {e}")
        raise

async def shutdown():
    """Остановка бота"""
    try:
        await bot.session.close()
        logger.info("✅ Bot shutdown complete.")
    except Exception as e:
        logger.error(f"❌ Shutdown error: {e}")

# =============================================================================
# MAIN
# =============================================================================

if __name__ == "__main__":
    import uvicorn
    import os
    
    # БЕРЁМ ПОРТ ИЗ ПЕРЕМЕННОЙ ОКРУЖЕНИЯ (Render сам её устанавливает)
    port = int(os.environ.get("PORT", 8000))  # 8000 как fallback для локальной разработки
    
    logger.info(f"📡 Starting server on port {port}...")
    uvicorn.run(app, host="0.0.0.0", port=port)
