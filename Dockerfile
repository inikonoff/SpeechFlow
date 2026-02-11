FROM python:3.11-slim

WORKDIR /app

# Только базовые зависимости, ffmpeg НЕ НУЖЕН!
RUN apt-get update && apt-get install -y \
    git \
    build-essential \
    && rm -rf /var/lib/apt/lists/*

# Обновляем pip
RUN pip install --no-cache-dir --upgrade pip setuptools wheel

# Копирование зависимостей
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Копирование исходного кода
COPY src/ ./src/

# Устанавливаем PYTHONPATH
ENV PYTHONPATH=/app

# Запуск приложения
CMD ["python", "-m", "src.main"]
