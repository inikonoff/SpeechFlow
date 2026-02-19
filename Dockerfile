FROM python:3.10-slim

WORKDIR /app

# Копирование файла зависимостей
COPY requirements.txt .

# Установка зависимостей
RUN pip install --no-cache-dir -r requirements.txt

# Копирование всей папки src в /app/src
COPY src/ ./src/

# Создание непривилегированного пользователя
RUN useradd -m -u 1000 appuser && chown -R appuser:appuser /app
USER appuser

# Переменные окружения
ENV GROQ_API_KEY=""
ENV PORT=8000
ENV HOST=0.0.0.0

# Здоровье контейнера
HEALTHCHECK --interval=30s --timeout=10s --start-period=5s --retries=3 \
    CMD python -c "import requests; requests.get('http://localhost:8000/health')" || exit 1

EXPOSE 8000

# Запускаем main.py из папки src
CMD ["python", "src/main.py"]