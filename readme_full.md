# 🗣 SpeechFlow AI Bot

An advanced AI-powered English conversational tutor built for Telegram. SpeechFlow acts as a charismatic speaking partner, analyzing voice and text inputs in real-time, correcting grammar, building custom vocabularies, and replying with natural synthetic voice.

## 🌟 Features

*   **Real-time STT & TTS:** Uses Groq Whisper for lightning-fast speech-to-text and Orpheus/OpenAI for natural text-to-speech.
*   **Adaptive LLM Pedagogy:** Powered by `llama3-70b-8192`. The AI adapts its vocabulary, sentence length, and grammar complexity based on the user's level (Beginner to Advanced).
*   **Surgical Corrections:** The bot doesn't just chat; it catches mistakes, explains *why* they are wrong (in Russian), and provides correct alternatives.
*   **Smart Vocabulary Builder:** Automatically extracts new idioms and words from the conversation and saves them to your personal Supabase database.
*   **Gamification & Stats:** Tracks your daily streak, message counts, and categorizes your most frequent grammar/vocabulary mistakes.
*   **Customizable AI Voice:** Choose your preferred AI tutor's voice directly from the Telegram menu (`/voice`).

## 🛠 Tech Stack

*   **Framework:** Python 3.11+, Aiogram 3.x, FastAPI
*   **AI Engine:** Groq API (Whisper + LLaMA3)
*   **Database:** Supabase (PostgreSQL)
*   **Deployment:** Ready for Render / Heroku
*   **Monitoring:** Built-in Uptime endpoints and Prometheus `/metrics` exporter.

## 🚀 Getting Started

### 1. Environment Variables (`.env`)
Create a `.env` file in the root directory:
```env
TELEGRAM_BOT_TOKEN=your_telegram_bot_token
GROQ_API_KEYS=key1,key2,key3 # Supports multiple keys for round-robin balancing
SUPABASE_URL=your_supabase_url
SUPABASE_KEY=your_supabase_anon_key
ADMIN_IDS=123456789,987654321
DEFAULT_USER_LEVEL=intermediate
FREE_MESSAGES_LIMIT=0
VOICE_RESPONSE_MODE=mirror # "always", "mirror", or "never"
TTS_VOICE=austin
