# 🗣️ Speech Flow AI

**Telegram-бот для разговорного английского. Живой диалог, умная обратная связь, голосовые ответы.**

[@speech_flow_bot](https://t.me/speech_flow_bot)

---

## What is it? / Что это?

Speech Flow AI — это не языковое приложение с уроками и тестами. Это собеседник. Точнее, шестеро собеседников — каждый со своей историей, характером и голосом. Вы просто разговариваете. На английском. А бот слушает, отвечает и незаметно помогает говорить лучше.

Speech Flow AI is not a language app with lessons and tests. It's a conversation partner — six of them, actually, each with their own story, personality, and voice. You just talk. In English. The bot listens, responds, and quietly helps you speak better.

---

## Features / Возможности

### 🎤 Voice-first
Send voice messages — the bot transcribes, responds in kind. Text works too. Every response comes with audio and a translate button.

### 🧠 Normal Mode
Every message you send is analyzed: grammar corrected, vocabulary saved to your personal dictionary, explanation given in Russian. The conversation keeps flowing naturally while the learning happens underneath.

### ▶️ Flow Mode
Six real conversation partners. No corrections, no analysis — just talk. Choose who you want to talk to: a med student, a chef, a programmer, a teacher, a travel blogger, or a mom of twins. Each one has a personality, a voice, and a life. Switch mid-conversation and your new partner will pick up the thread.

### 🧬 Long-term Memory
Your conversation partners remember you. Next time you talk, they already know what matters to you.

### 📊 Stats & Vocabulary - 
Track your streak, error patterns, and personal word list. New vocabulary is saved automatically from every conversation.

---

## How it works / Как это работает

```
/start → choose your level → choose your partner → start talking
```

In **Normal Mode**: speak or type → get correction + explanation + conversation response.

In **Flow Mode**: press ▶ Flow → choose a partner → talk freely → press Switch to change partners → press ⏹ Stop Flow to return to Normal Mode.

---

## Project Structure / Структура проекта

```
speech-flow-ai/
├── src/
│   ├── bot/
│   │   └── handlers/
│   │       ├── start.py        # Onboarding, persona selection
│   │       ├── message.py      # Normal mode, Flow mode, summarization
│   │       ├── menu.py         # Commands, stats, vocabulary
│   │       └── level.py        # Level change
│   │   └── keyboards.py        # All keyboards and inline buttons
│   ├── services/
│   │   ├── groq_client.py      # LLM, TTS, transcription, summarization
│   │   └── supabase_db.py      # All database operations
│   ├── utils/
│   │   └── audio.py            # Voice file handling
│   ├── personas.py             # Six conversation partners with full prompts
│   ├── config.py               # Settings and environment variables
│   └── main.py                 # Bot entry point
├── migration.sql               # Full database schema
├── Dockerfile
├── requirements.txt
└── .env.example
```

---

## Setup / Запуск

```bash
# Clone
git clone https://github.com/yourusername/speech-flow-ai.git
cd speech-flow-ai

# Install dependencies
pip install -r requirements.txt

# Configure
cp .env.example .env
# Fill in TELEGRAM_BOT_TOKEN, GROQ_API_KEYS, SUPABASE_URL, SUPABASE_KEY

# Run database migration
# Execute migration.sql in your Supabase SQL Editor

# Start
python src/main.py
```

---

## The Characters / Персонажи

| Name | Voice | Role |
|------|-------|------|
| Greg | austin | Med student |
| Mark | troy | Chef |
| Junior | daniel | Programmer |
| Mrs. Smith | diana | English teacher |
| Summer | autumn | Travel blogger |
| Jane | hannah | Mom on maternity leave |

Each character has a backstory, relationships with each other, and a distinct way of speaking. They remember what you tell them.

---

## Roadmap

- [x] Voice + text conversations
- [x] Level-adaptive corrections
- [x] Stats and personal vocabulary
- [x] Flow Mode with character selection
- [x] Long-term memory via session summaries
- [ ] Session summaries for Normal Mode
- [ ] Vocabulary export (PDF / CSV)
- [ ] Daily conversation challenges
- [ ] Progressive character unlocks

---

*Built by [@inikonoff](https://t.me/inikonoff)*
