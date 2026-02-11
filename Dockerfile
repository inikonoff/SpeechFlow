FROM python:3.11-slim

WORKDIR /app

# Установка системных зависимостей (только необходимое)
RUN apt-get update && apt-get install -y \
    git \
    build-essential \
    && rm -rf /var/lib/apt/lists/*

# Обновляем pip и устанавливаем базовые пакеты
RUN pip install --no-cache-dir --upgrade pip setuptools wheel

# Копирование зависимостей
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Копирование исходного кода
COPY src/ ./src/

# Запуск приложения
CMD ["python", "src/main.py"]
