import logging
import asyncio
from contextlib import asynccontextmanager

from fastapi import FastAPI
from aiogram import Bot, Dispatcher
from aiogram.enums import ParseMode
from aiogram.client.default import DefaultBotProperties

from src.config import settings
from src.bot.handlers import start, level, menu, message
from src.bot.middlewares.user_middleware import UserMiddleware

# Настройка логирования
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger(__name__)

# Создаем FastAPI приложение для health checks
@asynccontextmanager
async def lifespan(app: FastAPI):
    """Lifespan для FastAPI"""
    # Старт бота
    await startup()
    yield
    # Остановка бота
    await shutdown()

app = FastAPI(lifespan=lifespan)
bot = Bot(
    token=settings.TELEGRAM_BOT_TOKEN,
    default=DefaultBotProperties(parse_mode=ParseMode.MARKDOWN)
)
dp = Dispatcher()


@app.get("/health")
async def health_check():
    """Health check endpoint для Render/UptimeRobot"""
    return {"status": "alive", "service": "speech-flow-bot"}


async def startup():
    """Запуск бота"""
    logger.info("Starting Speech Flow AI Bot...")
    
    # Регистрируем middleware
    dp.update.middleware(UserMiddleware())
    
    # Регистрируем роутеры
    dp.include_router(start.router)
    dp.include_router(level.router)
    dp.include_router(menu.router)
    dp.include_router(message.router)
    
    # Удаляем вебхук (если был)
    await bot.delete_webhook(drop_pending_updates=True)
    
    # Запускаем polling в фоне
    asyncio.create_task(dp.start_polling(bot))
    
    logger.info("Bot started successfully!")


async def shutdown():
    """Остановка бота"""
    logger.info("Shutting down Speech Flow AI Bot...")
    await bot.session.close()
    logger.info("Bot shutdown complete.")


if __name__ == "__main__":
    # Для локального запуска
    import uvicorn
    
    logger.info("Starting in local mode...")
    uvicorn.run(app, host="0.0.0.0", port=8000)
