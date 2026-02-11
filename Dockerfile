FROM python:3.11-slim

WORKDIR /app

# Установка системных зависимостей
RUN apt-get update && apt-get install -y \
    git \
    build-essential \
    && rm -rf /var/lib/apt/lists/*

# Обновляем pip
RUN pip install --no-cache-dir --upgrade pip setuptools wheel

# Копирование и установка зависимостей
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Копирование исходного кода
COPY src/ ./src/

# ВАЖНО: Устанавливаем PYTHONPATH для корректного импорта
ENV PYTHONPATH=/app

# Запуск приложения
CMD ["python", "-m", "src.main"]
