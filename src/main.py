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
dp = Dispatcher()
shutdown_event = asyncio.Event()
start_time = time.time()
polling_task = None
is_shutting_down = False
request_stats: Dict[str, int] = {
    "total": 0,
    "success": 0,
    "errors": 0
}


# ============================================================================
# ОБРАБОТКА СИГНАЛОВ (SIGTERM)
# ============================================================================

def handle_sigterm(signum, frame):
    """Обработчик сигнала SIGTERM от Render"""
    global is_shutting_down
    if is_shutting_down:
        return
    
    logger.info("📡 Received SIGTERM signal, initiating graceful shutdown...")
    is_shutting_down = True
    
    # Создаем задачу для асинхронного завершения
    loop = asyncio.get_running_loop()
    loop.call_soon_threadsafe(lambda: asyncio.create_task(trigger_shutdown()))


async def trigger_shutdown():
    """Триггер для graceful shutdown"""
    shutdown_event.set()

# ============================================================================
# МОНИТОРИНГ И HEALTHCHECK
# ============================================================================

def get_system_stats() -> Dict[str, Any]:
    """Получение статистики системы"""
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
    """Проверка здоровья сервисов"""
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
    """Lifespan для FastAPI с улучшенным мониторингом"""
    global polling_task
    
    logger.info("🚀 Starting Speech Flow AI Bot...")
    logger.info("=" * 50)
    logger.info(f"📊 Initializing with:")
    logger.info(f"   • Groq clients: {len(groq_client.clients) if hasattr(groq_client, 'clients') else 0}")
    logger.info(f"   • Admin IDs: {len(ADMIN_IDS)}")
    logger.info(f"   • Voice mode: {settings.VOICE_RESPONSE_MODE}")
    logger.info(f"   • TTS voice: {settings.TTS_VOICE}")
    logger.info("=" * 50)
    
    # Создаем временную директорию
    temp_dir = getattr(settings, 'TEMP_DIR', '/tmp/speech_flow')
    os.makedirs(temp_dir, exist_ok=True)
    settings.TEMP_DIR = temp_dir
    
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
    polling_task = asyncio.create_task(run_polling_with_auto_restart())
    
    # Регистрируем обработчик SIGTERM
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(sig, handle_sigterm, sig, None)
    logger.info("✅ Signal handlers registered")
    
    yield  # Здесь работает приложение
    
    # Graceful shutdown
    logger.info("🛑 Shutting down Speech Flow AI Bot...")
    await shutdown()


app = FastAPI(
    lifespan=lifespan,
    title="Speech Flow AI Bot",
    version="1.0.0",
    docs_url=None,
    redoc_url=None
)

# =============================================================================
# УЛУЧШЕННЫЕ ENDPOINTЫ ДЛЯ МОНИТОРИНГА
# =============================================================================

@app.get("/")
async def root():
    """Корневой эндпоинт с базовой информацией"""
    uptime_seconds = int(time.time() - start_time)
    uptime_str = str(timedelta(seconds=uptime_seconds))
    
    return {
        "status": "alive",
        "service": "Speech Flow AI",
        "version": "1.0.0",
        "environment": os.getenv("RENDER_SERVICE_NAME", "development"),
        "uptime": uptime_str,
        "timestamp": datetime.utcnow().isoformat()
    }


@app.get("/health")
@app.head("/health")  # Добавляем поддержку HEAD запросов для Render
async def health_check():
    """
    Health check endpoint для Render/UptimeRobot
    Возвращает 200 если всё OK, иначе 503
    Поддерживает GET и HEAD методы
    """
    try:
        # Проверяем базовую доступность
        services = check_services_health()
        system_stats = get_system_stats()
        
        # Проверяем, что polling работает
        polling_healthy = polling_task is not None and not polling_task.done()
        
        # Критические сервисы
        critical_services = ["groq", "supabase"]
        all_critical_ok = all(services.get(svc, False) for svc in critical_services)
        
        # Общий статус
        health_status = all_critical_ok and polling_healthy
        
        response_data = {
            "status": "healthy" if health_status else "degraded",
            "timestamp": datetime.utcnow().isoformat(),
            "uptime_seconds": int(time.time() - start_time),
            "services": services,
            "polling": polling_healthy,
            "requests": request_stats
        }
        
        status_code = 200 if health_status else 503
        return Response(
            content=json.dumps(response_data),
            media_type="application/json",
            status_code=status_code
        )
        
    except Exception as e:
        logger.error(f"Health check error: {e}")
        return Response(
            content=json.dumps({
                "status": "unhealthy",
                "error": str(e),
                "timestamp": datetime.utcnow().isoformat()
            }),
            media_type="application/json",
            status_code=503
        )


@app.get("/ping")
async def ping():
    """Простой ping endpoint для быстрой проверки"""
    return {
        "pong": True,
        "timestamp": datetime.utcnow().isoformat()
    }


@app.get("/status")
async def status():
    """Детальный статус бота (только для админов)"""
    try:
        bot_info = await bot.get_me()
        system_stats = get_system_stats()
        services = check_services_health()
        
        # Информация о диалогах (если есть)
        from src.bot.handlers.message import document_dialogues
        
        return {
            "status": "running",
            "bot": {
                "username": bot_info.username,
                "id": bot_info.id,
                "name": bot_info.first_name,
                "is_bot": True
            },
            "config": {
                "groq_clients": len(groq_client.clients) if hasattr(groq_client, 'clients') else 0,
                "admin_count": len(ADMIN_IDS),
                "voice_mode": settings.VOICE_RESPONSE_MODE,
                "tts_voice": settings.TTS_VOICE,
                "free_messages_limit": settings.FREE_MESSAGES_LIMIT
            },
            "services": services,
            "system": system_stats,
            "requests": request_stats,
            "active_dialogues": len(document_dialogues) if document_dialogues else 0,
            "polling_active": polling_task is not None and not polling_task.done(),
            "uptime_seconds": int(time.time() - start_time),
            "uptime_human": str(timedelta(seconds=int(time.time() - start_time))),
            "timestamp": datetime.utcnow().isoformat()
        }
        
    except Exception as e:
        logger.error(f"Status check error: {e}")
        return {
            "status": "error",
            "error": str(e),
            "timestamp": datetime.utcnow().isoformat()
        }


@app.get("/metrics")
async def metrics():
    """Prometheus-совместимые метрики"""
    system_stats = get_system_stats()
    
    metrics_text = f"""# HELP speech_flow_uptime_seconds Uptime in seconds
# TYPE speech_flow_uptime_seconds gauge
speech_flow_uptime_seconds {int(time.time() - start_time)}

# HELP speech_flow_requests_total Total requests processed
# TYPE speech_flow_requests_total counter
speech_flow_requests_total {request_stats['total']}

# HELP speech_flow_requests_success Successful requests
# TYPE speech_flow_requests_success counter
speech_flow_requests_success {request_stats['success']}

# HELP speech_flow_requests_errors Failed requests
# TYPE speech_flow_requests_errors counter
speech_flow_requests_errors {request_stats['errors']}

# HELP speech_flow_groq_clients Number of Groq API clients
# TYPE speech_flow_groq_clients gauge
speech_flow_groq_clients {len(groq_client.clients) if hasattr(groq_client, 'clients') else 0}

# HELP speech_flow_memory_rss_bytes Memory usage in bytes
# TYPE speech_flow_memory_rss_bytes gauge
speech_flow_memory_rss_bytes {system_stats.get('memory_rss', 0)}

# HELP speech_flow_cpu_percent CPU usage percent
# TYPE speech_flow_cpu_percent gauge
speech_flow_cpu_percent {system_stats.get('cpu_percent', 0)}

# HELP speech_flow_polling_active Polling status (1=active, 0=inactive)
# TYPE speech_flow_polling_active gauge
speech_flow_polling_active {1 if polling_task is not None and not polling_task.done() else 0}
"""
    return Response(
        content=metrics_text,
        media_type="text/plain"
    )


@app.get("/debug/health/detailed")
async def health_detailed():
    """Детальный health check для отладки"""
    try:
        # Проверяем Groq
        groq_health = False
        try:
            if hasattr(groq_client, 'clients') and groq_client.clients:
                # Простой тестовый запрос
                groq_health = True
        except:
            pass
        
        # Проверяем Supabase
        supabase_health = False
        try:
            # Простая проверка соединения
            result = db.client.table("users").select("id").limit(1).execute()
            supabase_health = result is not None
        except:
            pass
        
        # Проверяем файловую систему (временные файлы)
        temp_health = os.access(settings.TEMP_DIR, os.W_OK) if hasattr(settings, 'TEMP_DIR') else True
        
        # Проверяем polling
        polling_health = polling_task is not None and not polling_task.done()
        
        # Проверяем зависимости
        dependencies = {
            "groq": groq_health,
            "supabase": supabase_health,
            "temp_dir": temp_health,
            "polling": polling_health
        }
        
        all_healthy = all(dependencies.values())
        
        return {
            "status": "healthy" if all_healthy else "degraded",
            "timestamp": datetime.utcnow().isoformat(),
            "dependencies": dependencies,
            "system": get_system_stats(),
            "is_shutting_down": is_shutting_down
        }
        
    except Exception as e:
        return {
            "status": "error",
            "error": str(e),
            "timestamp": datetime.utcnow().isoformat()
        }

# =============================================================================
# ЗАПУСК POLLING С АВТОМАТИЧЕСКИМ ПЕРЕЗАПУСКОМ
# =============================================================================

async def run_polling_with_auto_restart():
    """Запуск polling с автоматическим перезапуском при ошибках"""
    global is_shutting_down
    
    while not is_shutting_down:
        try:
            logger.info("🚀 Starting polling...")
            await dp.start_polling(bot)
            logger.info("✅ Polling completed normally")
            # break  # Выходим если polling завершился нормально
        except asyncio.CancelledError:
            logger.info("📡 Polling task cancelled")
            break
        except Exception as e:
            if is_shutting_down:
                break
            
            logger.error(f"❌ Polling error: {e}")
            request_stats["errors"] += 1
            
            # Ждем перед перезапуском
            logger.info("⏳ Waiting 5 seconds before restarting polling...")
            await asyncio.sleep(5)
            logger.info("🔄 Restarting polling...")
    
    logger.info("📡 Polling stopped")


async def shutdown():
    """Остановка бота"""
    global polling_task, is_shutting_down
    
    is_shutting_down = True
    
    try:
        # Отменяем polling задачу
        if polling_task and not polling_task.done():
            logger.info("⏳ Cancelling polling task...")
            polling_task.cancel()
            try:
                await asyncio.wait_for(polling_task, timeout=10)
                logger.info("✅ Polling task cancelled")
            except (asyncio.CancelledError, asyncio.TimeoutError):
                logger.info("⏱️ Polling task cancellation timeout")
        
        # Даём время на завершение задач
        logger.info("⏳ Waiting for ongoing tasks to complete (up to 25 seconds)...")
        try:
            await asyncio.wait_for(shutdown_event.wait(), timeout=25)
            logger.info("✅ Ongoing tasks completed")
        except asyncio.TimeoutError:
            logger.info("⏱️ Shutdown timeout reached, forcing close...")
        
        # Закрываем сессию бота
        await bot.session.close()
        logger.info("✅ Bot session closed")
        
    except Exception as e:
        logger.error(f"❌ Shutdown error: {e}")

# =============================================================================
# MIDDLEWARE ДЛЯ ПОДСЧЕТА СТАТИСТИКИ
# =============================================================================

@app.middleware("http")
async def stats_middleware(request: Request, call_next):
    """Middleware для подсчета запросов"""
    request_stats["total"] += 1
    try:
        response = await call_next(request)
        if response.status_code < 400:
            request_stats["success"] += 1
        else:
            request_stats["errors"] += 1
        return response
    except Exception as e:
        request_stats["errors"] += 1
        raise

# =============================================================================
# MAIN
# =============================================================================

if __name__ == "__main__":
    import uvicorn
    import os

    # Создаем временную директорию, если нужно
    temp_dir = getattr(settings, 'TEMP_DIR', '/tmp/speech_flow')
    os.makedirs(temp_dir, exist_ok=True)
    settings.TEMP_DIR = temp_dir

    # Берём порт из переменной окружения, преобразуем в int (обязательно!)
    # Если переменная не найдена, используем 10000 для локальной разработки
    port = int(os.environ.get("PORT", 10000))

    logger.info("=" * 60)
    logger.info(f"📡 Starting Speech Flow AI Bot in production mode...")
    logger.info(f"📌 PORT: {port}")
    logger.info(f"📁 Temp dir: {temp_dir}")
    logger.info(f"🔧 Health check: http://localhost:{port}/health (supports GET and HEAD)")
    logger.info(f"📊 Metrics: http://localhost:{port}/metrics")
    logger.info(f"📝 Status: http://localhost:{port}/status")
    logger.info("=" * 60)

    # Запускаем с правильными параметрами для Render
    # ВАЖНО: указываем путь к модулю с точкой: "src.main:app"
    uvicorn.run(
        "src.main:app",  # <--- ИСПРАВЛЕНО: теперь указываем папку и файл
        host="0.0.0.0",  # <--- ОБЯЗАТЕЛЬНО: слушаем все интерфейсы
        port=port,       # <--- Используем порт из переменной окружения
        log_level="info",
        reload=False,
        workers=1
    )
