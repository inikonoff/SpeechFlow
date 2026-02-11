FROM python:3.11-slim

WORKDIR /app

# Установка ffmpeg для Whisper
RUN apt-get update && apt-get install -y \
    ffmpeg \
    git \
    && rm -rf /var/lib/apt/lists/*

# Копирование зависимостей
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Копирование исходного кода
COPY src/ ./src/

# Запуск приложения
CMD ["python", "src/main.py"]
