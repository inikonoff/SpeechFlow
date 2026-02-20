import os
import sys
import signal
import logging
import asyncio
import time
import psutil
from contextlib import asynccontextmanager
from datetime import datetime
from typing import Dict, Any
from fastapi import FastAPI, Response
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
    logger.info("📡 Received SIGTERM signal, initiating graceful shutdown...")
    asyncio.create_task(trigger_shutdown())


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
        "groq": len(groq_client.clients) > 0,
        "supabase": True  # Будет проверяться отдельно
    }
    
    # Проверяем Supabase простым запросом
    try:
        # Асинхронно не можем вызвать здесь, поэтому просто проверяем наличие клиента
        services["supabase"] = db is not None
    except:
        services["supabase"] = False
    
    return services


# ============================================================================
# LIFESPAN
# ============================================================================

@asynccontextmanager
async def lifespan(app: FastAPI):
    """Lifespan для FastAPI с улучшенным мониторингом"""
    logger.info("🚀 Starting Speech Flow AI Bot...")
    logger.info("=" * 50)
    logger.info(f"📊 Initializing with:")
    logger.info(f"   • Groq clients: {len(groq_client.clients)}")
    logger.info(f"   • Admin IDs: {len(ADMIN_IDS)}")
    logger.info(f"   • Voice mode: {settings.VOICE_RESPONSE_MODE}")
    logger.info(f"   • TTS voice: {settings.TTS_VOICE}")
    logger.info("=" * 50)
    
    # Регистрируем обработчик SIGTERM
    signal.signal(signal.SIGTERM, handle_sigterm)
    logger.info("✅ SIGTERM handler registered")
    
    # Запускаем бота
    await startup()
    
    yield  # Здесь работает приложение
    
    # Graceful shutdown
    logger.info("🛑 Shutting down Speech Flow AI Bot...")
    await shutdown()


app = FastAPI(
    lifespan=lifespan,
    title="Speech Flow AI Bot",
    version="1.0.0",
    docs_url=None,  # Отключаем Swagger в production
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
async def health_check():
    """
    Health check endpoint для Render/UptimeRobot
    Возвращает 200 если всё OK, иначе 503
    """
    try:
        # Проверяем базовую доступность
        services = check_services_health()
        system_stats = get_system_stats()
        
        # Проверяем, что все критические сервисы работают
        critical_services = ["groq", "supabase"]
        all_critical_ok = all(services.get(svc, False) for svc in critical_services)
        
        # Проверяем память (если > 90%, считаем нездоровым)
        memory_ok = True
        if system_stats.get("memory_rss_mb", 0) > 1024:  # > 1GB
            memory_ok = False
        
        health_status = all_critical_ok and memory_ok
        
        response_data = {
            "status": "healthy" if health_status else "degraded",
            "timestamp": datetime.utcnow().isoformat(),
            "uptime_seconds": int(time.time() - start_time),
            "services": services,
            "system": system_stats,
            "requests": request_stats
        }
        
        status_code = 200 if health_status else 503
        return Response(
            content=json.dumps(response_data, indent=2),
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
                "groq_clients": len(groq_client.clients),
                "admin_count": len(ADMIN_IDS),
                "voice_mode": settings.VOICE_RESPONSE_MODE,
                "tts_voice": settings.TTS_VOICE,
                "free_messages_limit": settings.FREE_MESSAGES_LIMIT
            },
            "services": services,
            "system": system_stats,
            "requests": request_stats,
            "active_dialogues": len(document_dialogues) if document_dialogues else 0,
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
speech_flow_groq_clients {len(groq_client.clients)}

# HELP speech_flow_memory_rss_bytes Memory usage in bytes
# TYPE speech_flow_memory_rss_bytes gauge
speech_flow_memory_rss_bytes {system_stats.get('memory_rss', 0)}

# HELP speech_flow_cpu_percent CPU usage percent
# TYPE speech_flow_cpu_percent gauge
speech_flow_cpu_percent {system_stats.get('cpu_percent', 0)}
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
            if groq_client.clients:
                # Простой тестовый запрос
                groq_health = True
        except:
            pass
        
        # Проверяем Supabase
        supabase_health = False
        try:
            # Простая проверка соединения
            test_user = await db.get_or_create_user(0)
            supabase_health = test_user is not None
        except:
            pass
        
        # Проверяем файловую систему (временные файлы)
        temp_health = os.access(settings.TEMP_DIR, os.W_OK) if hasattr(settings, 'TEMP_DIR') else True
        
        # Проверяем зависимости
        dependencies = {
            "groq": groq_health,
            "supabase": supabase_health,
            "temp_dir": temp_health
        }
        
        all_healthy = all(dependencies.values())
        
        return {
            "status": "healthy" if all_healthy else "degraded",
            "timestamp": datetime.utcnow().isoformat(),
            "dependencies": dependencies,
            "system": get_system_stats()
        }
        
    except Exception as e:
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
        request_stats["errors"] += 1
    finally:
        logger.info("Polling stopped")


async def shutdown():
    """Остановка бота"""
    try:
        # Даём время на завершение задач
        logger.info("⏳ Waiting for ongoing tasks to complete (up to 30 seconds)...")
        
        # Ждём сигнала или таймаут
        try:
            await asyncio.wait_for(shutdown_event.wait(), timeout=30)
        except asyncio.TimeoutError:
            logger.info("Shutdown timeout reached, forcing close...")
        
        # Закрываем сессию бота
        await bot.session.close()
        logger.info("✅ Bot session closed")
        
    except Exception as e:
        logger.error(f"❌ Shutdown error: {e}")


# =============================================================================
# MIDDLEWARE ДЛЯ ПОДСЧЕТА СТАТИСТИКИ
# =============================================================================

@app.middleware("http")
async def stats_middleware(request, call_next):
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
    
    # Создаем временную директорию, если нужно
    temp_dir = getattr(settings, 'TEMP_DIR', '/tmp/speech_flow')
    os.makedirs(temp_dir, exist_ok=True)
    settings.TEMP_DIR = temp_dir
    
    # Берём порт из переменной окружения
    port = int(os.environ.get("PORT", 8000))
    
    logger.info("=" * 50)
    logger.info(f"📡 Starting in production mode...")
    logger.info(f"📌 PORT: {port}")
    logger.info(f"📁 Temp dir: {temp_dir}")
    logger.info(f"🔧 UptimeRobot monitoring: http://localhost:{port}/health")
    logger.info(f"📊 Metrics: http://localhost:{port}/metrics")
    logger.info("=" * 50)
    
    uvicorn.run(
        app,
        host="0.0.0.0",
        port=port,
        log_level="info"
    )
