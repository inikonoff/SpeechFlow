README.md:
markdown
# 🗣️ Speech Flow AI — Ваш персональный AI-репетитор английского

@speech_flow_bot

> **"Говорите свободно, ошибки — это часть пути"**

Speech Flow AI — это интеллектуальный Telegram-бот, который помогает улучшить разговорный английский через **естественный диалог**. Никакой зубрёжки, только живое общение с мгновенной обратной связью.

---

## ✨ **Ключевые возможности**

### 🎤 **Голосовые и текстовые сообщения**
- Отправляйте голосовые — бот распознаёт речь
- Отвечает голосом (реалистичные голоса)
- Дублирует всё текстом для удобства

### 📊 **Умная коррекция ошибок**
Бот анализирует вашу речь и даёт обратную связь, адаптированную под ваш уровень:

```python
Beginner: "Ты сказал 'I go', но после he/she нужно 'goes'"
Advanced: "Consider using present perfect here for past experiences"
📚 Персональный словарь
Новые слова автоматически добавляются в словарь

Отслеживайте прогресс в статистике

Контекстные примеры использования

📈 Детальная статистика
Уровень владения языком

Streak дней

Количество сообщений

Словарь и анализ ошибок по категориям

🎯 Адаптация под уровень
4 уровня сложности с разными подходами:

Уровень	Словарь	Грамматика	Длина ответов	Стиль объяснений
Beginner	Top 500 слов	Present/Past/Future Simple	5-8 слов	100% русский
Elementary	Top 1500 слов	Present Perfect, модальные глаголы	8-12 слов	60% русский
Intermediate	3000+ слов	Conditionals, пассивный залог	10-15 слов	30% русский
Advanced	Academic + idioms	Subjunctive, inversion	15-20 слов	100% английский
🚀 Как начать
Напишите боту: @SpeechFlowAI_bot

Выберите уровень: Beginner → Advanced

Начните диалог: Просто отправьте сообщение (текст или голос)

💡 Пример диалога:
text
👤 Пользователь: "Yesterday I go to cinema"
🤖 Speech Flow AI:

✅ **Correct**
"Yesterday I went to the cinema"

💡 **Why**
Past simple needed for completed actions (yesterday). 
Also add article "the" before specific cinema.

🎤 [голосовой ответ]
💬 "Oh, you went to the cinema yesterday? What movie did you see?"
🛠 Технологии
AI: Groq (Whisper, LLaMA 4 Scout, Orpheus TTS)

Backend: Python + aiogram 3.x

Database: Supabase (PostgreSQL)

API: FastAPI + Uvicorn

📦 Для разработчиков
Локальный запуск
bash
# 1. Клонировать репозиторий
git clone https://github.com/yourusername/speech-flow-ai.git
cd speech-flow-ai

# 2. Установить зависимости
pip install -r requirements.txt

# 3. Создать .env файл
cp .env.example .env
# Заполните TELEGRAM_BOT_TOKEN, GROQ_API_KEYS, SUPABASE_URL, SUPABASE_KEY

# 4. Запустить
python src/main.py

🎯 Дорожная карта
Базовый функционал (текст + голос)

Адаптивные промпты под уровень

Статистика и словарь

Экспорт словаря в PDF/CSV

Ежедневные уроки-челленджи

Мультиязычные объяснения (испанский, китайский)

Веб-версия для десктопа

🤝 Контрибьюция
PRы приветствуются! Если у вас есть идеи по улучшению промптов или функционала:

Fork репозитория

Создайте ветку (git checkout -b feature/amazing-idea)

Commit изменения (git commit -m 'Add amazing idea')

Push в ветку (git push origin feature/amazing-idea)

Откройте Pull Request

📄 Лицензия
MIT License — свободно используйте, модифицируйте и распространяйте.

🌟 Поддержите проект
⭐ Поставьте звезду на GitHub

👥 Расскажите друзьям, изучающим английский

🐛 Сообщайте об ошибках в Issues

Начните говорить свободно уже сегодня! 🗣️✨
