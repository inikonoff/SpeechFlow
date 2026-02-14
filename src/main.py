import logging
import asyncio
from contextlib import asynccontextmanager

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

app = FastAPI(lifespan=lifespan)
bot = Bot(
    token=settings.TELEGRAM_BOT_TOKEN,
    default=DefaultBotProperties(parse_mode=ParseMode.MARKDOWN)
)
dp = Dispatcher()


@app.get("/")
async def root():
    """Корневой эндпоинт"""
    return {
        "status": "alive", 
        "service": "Speech Flow AI",
        "version": "1.0.0",
        "message": "Bot is running!"
    }


@app.get("/health")
async def health_check():
    """Health check endpoint для Render/UptimeRobot"""
    return {
        "status": "healthy", 
        "service": "speech-flow-bot",
        "timestamp": asyncio.get_event_loop().time()
    }


@app.get("/ping")
async def ping():
    """Ping endpoint"""
    return {"pong": True}


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

@app.route('/health')
def health():
    return 'OK', 200

if __name__ == "__main__":
    import uvicorn
    logger.info("📡 Starting in local mode...")
    uvicorn.run(app, host="0.0.0.0", port=8000)
