import os
import sys
import signal
import logging
import asyncio
import time
import psutil
import json
from datetime import timedelta
from contextlib import asynccontextmanager
from datetime import datetime
from typing import Dict, Any
from fastapi import FastAPI, Response, Request
from fastapi.responses import JSONResponse
from aiogram import Bot, Dispatcher
from aiogram.enums import ParseMode
from aiogram.client.default import DefaultBotProperties
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import BotCommand

from src.config import settings, ADMIN_IDS
from src.bot.handlers import start, level, menu, message
from src.bot.middlewares.user_middleware import UserMiddleware
from src.services import groq_client, db

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
dp = Dispatcher(storage=MemoryStorage())  # FSM storage для Flow Mode
shutdown_event = asyncio.Event()
start_time = time.time()
polling_task = None
keep_alive_task = None
is_shutting_down = False
request_stats: Dict[str, int] = {
    "total": 0,
    "success": 0,
    "errors": 0
}

# ============================================================================
# МЕНЮ И АНТИ-СОН БД
# ============================================================================

async def setup_bot_commands(bot: Bot):
    """Установка системного меню слева от поля ввода"""
    bot_commands = [
        BotCommand(command="/start", description="Restart / Change Level"),
        BotCommand(command="/flow", description="Toggle Flow Mode"),
        BotCommand(command="/voice", description="Change AI Voice"),
        BotCommand(command="/stats", description="My Stats"),
        BotCommand(command="/vocabulary", description="My Vocabulary"),
        BotCommand(command="/author", description="Author")
    ]
    await bot.set_my_commands(bot_commands)
    logger.info("✅ Bot commands menu installed")

async def db_keep_alive():
    """Фоновая задача: пинг базы данных каждые 12 часов (Анти-сон)"""
    while True:
        try:
            await asyncio.sleep(43200)  # 12 часов
            await db.ping()
        except asyncio.CancelledError:
            break
        except Exception as e:
            logger.error(f"Keep-alive error: {e}")

# ============================================================================
# ОБРАБОТКА СИГНАЛОВ (SIGTERM)
# ============================================================================

def handle_sigterm(signum, frame):
    global is_shutting_down
    if is_shutting_down:
        return
    logger.info("📡 Received SIGTERM signal, initiating graceful shutdown...")
    is_shutting_down = True
    loop = asyncio.get_running_loop()
    loop.call_soon_threadsafe(lambda: asyncio.create_task(trigger_shutdown()))

async def trigger_shutdown():
    shutdown_event.set()

# ============================================================================
# МОНИТОРИНГ И HEALTHCHECK
# ============================================================================

def get_system_stats() -> Dict[str, Any]:
    try:
        process = psutil.Process()
        memory_info = process.memory_info()
        return {
            "cpu_percent": process.cpu_percent(interval=0.1),
            "memory_rss": memory_info.rss,
            "memory_rss_mb": memory_info.rss / 1024 / 1024,
            "memory_vms": memory_info.vms,
            "open_files": len(process.open_files()),
            "connections": len(process.connections()),
            "threads": process.num_threads()
        }
    except Exception as e:
        logger.error(f"Error getting system stats: {e}")
        return {}

def check_services_health() -> Dict[str, bool]:
    services = {
        "groq": len(groq_client.clients) > 0 if hasattr(groq_client, 'clients') else False,
        "supabase": db is not None
    }
    return services

# ============================================================================
# LIFESPAN
# ============================================================================

@asynccontextmanager
async def lifespan(app: FastAPI):
    global polling_task, keep_alive_task

    logger.info("🚀 Starting Speech Flow AI Bot...")
    logger.info("=" * 50)

    temp_dir = getattr(settings, 'TEMP_DIR', '/tmp/speech_flow')
    os.makedirs(temp_dir, exist_ok=True)
    settings.TEMP_DIR = temp_dir

    dp.update.middleware(UserMiddleware())

    dp.include_router(start.router)
    dp.include_router(level.router)
    dp.include_router(menu.router)
    dp.include_router(message.router)

    await bot.delete_webhook(drop_pending_updates=True)

    await setup_bot_commands(bot)
    keep_alive_task = asyncio.create_task(db_keep_alive())

    polling_task = asyncio.create_task(run_polling_with_auto_restart())

    loop = asyncio.get_running_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(sig, handle_sigterm, sig, None)
    logger.info("✅ Signal handlers registered")

    yield

    logger.info("🛑 Shutting down Speech Flow AI Bot...")
    if keep_alive_task and not keep_alive_task.done():
        keep_alive_task.cancel()

    await shutdown()

app = FastAPI(
    lifespan=lifespan,
    title="Speech Flow AI Bot",
    version="1.0.0",
    docs_url=None,
    redoc_url=None
)

# =============================================================================
# ENDPOINTS ДЛЯ МОНИТОРИНГА
# =============================================================================

@app.get("/")
async def root():
    uptime_seconds = int(time.time() - start_time)
    return {
        "status": "alive",
        "service": "Speech Flow AI",
        "version": "1.0.0",
        "uptime": str(timedelta(seconds=uptime_seconds)),
        "timestamp": datetime.utcnow().isoformat()
    }

@app.get("/health")
@app.head("/health")
async def health_check():
    try:
        services = check_services_health()
        polling_healthy = polling_task is not None and not polling_task.done()
        critical_services = ["groq", "supabase"]
        all_critical_ok = all(services.get(svc, False) for svc in critical_services)
        health_status = all_critical_ok and polling_healthy

        response_data = {
            "status": "healthy" if health_status else "degraded",
            "timestamp": datetime.utcnow().isoformat(),
            "uptime_seconds": int(time.time() - start_time),
            "services": services,
            "polling": polling_healthy,
        }
        status_code = 200 if health_status else 503
        return Response(content=json.dumps(response_data), media_type="application/json", status_code=status_code)
    except Exception as e:
        return Response(content=json.dumps({"status": "unhealthy", "error": str(e)}), media_type="application/json", status_code=503)

@app.get("/ping")
async def ping():
    return {"pong": True, "timestamp": datetime.utcnow().isoformat()}

@app.get("/status")
async def status():
    try:
        bot_info = await bot.get_me()
        return {
            "status": "running",
            "bot": {"username": bot_info.username, "id": bot_info.id},
            "system": get_system_stats(),
            "polling_active": polling_task is not None and not polling_task.done(),
            "uptime_seconds": int(time.time() - start_time)
        }
    except Exception as e:
        return {"status": "error", "error": str(e)}

@app.get("/metrics")
async def metrics():
    metrics_text = f"""# HELP speech_flow_uptime_seconds Uptime in seconds
# TYPE speech_flow_uptime_seconds gauge
speech_flow_uptime_seconds {int(time.time() - start_time)}
# HELP speech_flow_requests_total Total requests processed
# TYPE speech_flow_requests_total counter
speech_flow_requests_total {request_stats['total']}
"""
    return Response(content=metrics_text, media_type="text/plain")

@app.get("/debug/health/detailed")
async def health_detailed():
    return {"status": "ok"}

# =============================================================================
# ЗАПУСК POLLING
# =============================================================================

async def run_polling_with_auto_restart():
    global is_shutting_down
    while not is_shutting_down:
        try:
            logger.info("🚀 Starting polling...")
            await dp.start_polling(bot)
            logger.info("✅ Polling completed normally")
        except asyncio.CancelledError:
            break
        except Exception as e:
            if is_shutting_down:
                break
            logger.error(f"❌ Polling error: {e}")
            request_stats["errors"] += 1
            await asyncio.sleep(5)
    logger.info("📡 Polling stopped")

async def shutdown():
    global polling_task, is_shutting_down
    is_shutting_down = True
    try:
        if polling_task and not polling_task.done():
            polling_task.cancel()
            try:
                await asyncio.wait_for(polling_task, timeout=10)
            except:
                pass

        try:
            await asyncio.wait_for(shutdown_event.wait(), timeout=25)
        except:
            pass

        await bot.session.close()
    except Exception as e:
        logger.error(f"❌ Shutdown error: {e}")

@app.middleware("http")
async def stats_middleware(request: Request, call_next):
    request_stats["total"] += 1
    try:
        response = await call_next(request)
        if response.status_code < 400:
            request_stats["success"] += 1
        else:
            request_stats["errors"] += 1
        return response
    except Exception:
        request_stats["errors"] += 1
        raise

if __name__ == "__main__":
    import uvicorn
    temp_dir = getattr(settings, 'TEMP_DIR', '/tmp/speech_flow')
    os.makedirs(temp_dir, exist_ok=True)
    settings.TEMP_DIR = temp_dir
    port = int(os.environ.get("PORT", 10000))
    uvicorn.run("src.main:app", host="0.0.0.0", port=port, log_level="info", reload=False, workers=1)
