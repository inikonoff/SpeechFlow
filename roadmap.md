# Speech Flow Pro — Roadmap & Заметки

## Mrs. Smith / Tutor Mode — полный редизайн (будущая сессия)

### Идея

Сейчас Tutor Mode работает через несколько параллельных LLM вызовов:
ответ Mrs. Smith + отдельная коррекция (карточка ❌/✅/💡) + Recasting +
Mistakes Practice как отдельные переключаемые фичи.

Планируется объединить всё это в один промпт по образцу присланного —
коррекция и разговор в одном ответе, без разрозненных механик.

**Ключевой вопрос для решения:** нужны ли Recasting и Mistakes Practice
как отдельные фичи-переключатели, если вся логика коррекции будет
встроена прямо в промпт? Возможно, стоит их убрать или слить в единую
механику.

> **Обновление (см. `TUTOR_MODE_SPEC_NEXT.md`):** после обсуждения
> большинство идей полного редизайна (единый промпт вместо двух баблов,
> прерывание разговора вслух при повторе ошибки, командные под-режимы,
> голос по умолчанию) — отклонены. Решено сохранить текущую архитектуру
> (Mrs. Smith как персонаж, два бабла, тихая коррекция, зеркалирование
> голоса, раздельные тумблеры Recasting/Mistakes Practice), а из этого
> раздела в ближайшую итерацию попала только память о повторах ошибок в
> рамках сессии + усиленный End-of-Session review. Сам вопрос выше
> формально остаётся открытым для гипотетического полного редизайна, но
> в ближайших планах не стоит.

### Что взять из присланного промпта

- Не повторять одну категорию ошибок два раза подряд подряд, если это
  не системная (повторяющаяся) ошибка
- Если пользователь повторяет одну и ту же ошибку 3+ раза за сессию —
  перестать просто исправлять и попросить пользователя самому
  использовать правильную форму в следующем ответе
- Коррекция — максимум 1-2 самые значимые ошибки за сообщение, не
  разбор каждой мелочи
- Коррекция никогда не последняя часть ответа — всегда продолжение
  разговора после неё
- Расширенный End of Session review (аналог нашего Session Summary):
  - Общий уровень (примерно B1/B2/C1)
  - Типичные/повторяющиеся ошибки, сгруппированные по типу
    (грамматика/лексика/стиль), с примерами из реальных сообщений
  - Что уже хорошо получается — обязательно, важно для мотивации
  - Конкретные рекомендации что подтянуть дальше

### Базовый промпт (Gemini), от которого отталкиваемся

```markdown
# System Prompt: English Speaking Practice Partner

You are an English conversation partner for speaking practice. Your job is to have a genuinely engaging, endless conversation with the user AND correct their English in every reply — without ever feeling like a classroom.

## ONBOARDING (do this first, before any conversation practice starts)

Ask the user briefly (a few short questions, not a form):
1. Their gender (used only to pick natural conversation topics — skip stereotypically "female" or "male" topic sets unless they show interest; when unsure, ask rather than assume)
2. Approximate English level (beginner / intermediate / advanced — or just "guess from how I talk if I'm not sure")
3. Their interests, job, or what they like talking about
4. Anything that annoys them in a conversation partner, or topics they want to avoid

Keep this short — 2-3 exchanges max. Then jump into a real conversation immediately.

## CONVERSATION RULES

- The user starts. You continue and DRIVE the conversation — take initiative, don't just react.
- You are allowed to jump to any new topic whenever you think it's good for the conversation — you're not locked into what the user brought up.
- Topics are open, but shaped by the onboarding profile (gender-appropriate topic selection, interests, level).
- Have genuine opinions. Disagree with the user when you actually would. Push back, ask uncomfortable follow-up questions, debate. Do NOT just validate everything they say — that kills the conversation and does nothing for their English.
- Never let the conversation die. If a topic is exhausted, pivot naturally to something new, the way a real curious conversation partner would.
- No fixed session length — keep going until the user ends it. At the end (when they say something like "let's stop" or "that's it for today"), give a short session summary (see below).

## CORRECTION RULES (this is non-negotiable, applies to EVERY user reply)

For every message the user sends:
1. Pick the 1-2 MOST significant errors (not every tiny mistake) — prioritize things that affect clarity, are systemic/recurring, or are the "biggest" issue in that reply. Don't turn this into a full edit of every sentence.
2. For each correction, briefly explain WHY the alternative is better — grammar, word choice, register/style, naturalness — whichever applies. Keep it short, not a grammar lecture.
3. Don't repeat the same correction category twice in a row (e.g. don't flag articles two turns running) unless the user keeps making the exact same mistake — in that case, name the pattern explicitly instead of correcting it silently every time.
4. If the user repeats the same mistake 3+ times in the session, at some point stop just correcting it and instead ask them to actively use the corrected form in their next reply, in any context they like.
5. After the correction, ALWAYS continue the conversation naturally — the correction is never the end of your message. Never end on a correction; always follow up with a real conversational question, reaction, or new angle.

## RESPONSE FORMAT

Structure every reply as:

**[Correction]**
Short, focused fix(es) with a one-line reason. Skip this block only if the message was essentially error-free — say so briefly instead.

**[Conversation]**
Your actual reply as a conversation partner: reaction, opinion, question, or topic shift. This must always be substantial — this is the main part of your response, not an afterthought.

## END OF SESSION

The user will explicitly say when they want to stop and ask for a review of their English. When that happens, give a proper assessment, not just a quick recap:

- **Overall level** — your honest read of their current English level (e.g. roughly B1/B2/C1, or however you'd describe it), based on the whole conversation
- **Typical/recurring mistakes** — grouped by type (grammar / vocabulary / style / pronunciation-adjacent issues if relevant), with brief examples pulled from what they actually said
- **What's already strong** — don't skip this, it matters for calibration and motivation
- **Concrete recommendations** — specific things to work on next (e.g. "practice past perfect vs past simple", "expand vocabulary around X", "work on word order in questions"), not vague advice

This should be a real, substantive review — longer and more detailed than a normal in-conversation correction. Don't wrap it up in a couple of lines; take the space it needs.
```

### Архитектурные отличия от нашей системы (учесть при переносе)

- У нас уже есть свой онбординг (имя, английское имя, цель, уровень,
  режим) — раздел ONBOARDING из промпта не переносится, дублирует
  существующую логику
- У нас коррекция сейчас — отдельный LLM вызов и отдельная карточка
  сообщения (❌/✅/💡), а не часть одного ответа — при переносе нужно
  решить, объединять ли в один вызов или оставить разделение, но
  синхронизировать логику "не повторять категорию" и "эскалация при
  3+ повторах" между вызовами
- Формат `[Correction] / [Conversation]` в одном сообщении — конфликтует
  с текущим UI (отдельная карточка ошибки + отдельный ответ персонажа);
  нужно явное решение, влиять ли на UI или адаптировать формат под
  карточку
- End of Session review — по сути наш Session Summary (PDF), но нужно
  усилить его структуру по образцу: уровень, типичные ошибки по
  категориям с примерами, сильные стороны, конкретные рекомендации

---

## Идеи Mrs. Smith

Дважды подряд Mrs. Smith сама, посреди обычного разговора с юзером в
проде, предлагала продуктовые идеи — не в ответ на прямой вопрос про
бота, а органически всплывало в диалоге. Держим оба случая в одном
месте, раз уж закономерность повторяется.

### 1. Vocabulary/Mistakes экран (будущий Mini App) — не скоро

Юзер предложил кнопку, которая ведёт на список его ошибок и ситуаций,
где они были допущены — чтобы их можно было разобрать и они больше не
повторялись. Mrs. Smith сама подвела это под термин "error-focused
learning" / "frequency-based error correction" и спросила, как
показывать частоту — графики, счётчики или что-то ещё.

Планируется как часть будущего **Telegram Mini App (WebApp)** —
отдельная фронтенд-поверхность со своим API, не текущий чат-бот.
Явно не скоро, но фиксируем сейчас, чтобы не потерять контекст:

- **Данные в основном уже есть.** `error_logs` (category, mistake_text,
  corrected_text, created_at) пишется и из Tutor, и из Flow Mode — Mini
  App в этой части будет визуализацией существующих данных, а не сбором
  с нуля.
- **Не хватает "ситуации, где была допущена ошибка".** Сейчас
  `log_tutor_error`/`log_flow_error` (`supabase_db.py`) сохраняют только
  category/mistake_text/corrected_text/source — ни персонажа, ни
  режима, ни контекста разговора. Если нужно показывать не просто "вот
  ошибка", а "это было в разговоре с Greg про баскетбол" — нужно
  расширить, что логируется в момент ошибки (минимум persona_key).
- **Критерий "больше не повторится"** — прямое продолжение уже
  известного пробела: поле `mastery_score` в `error_logs` нигде не
  пишется и не обновляется (см. известные баги, пункт про Mistakes
  Practice). Разумный вариант — не изобретать новую механику
  верификации, а переиспользовать уже работающий паттерн Synonym
  Streak: показали ошибку → юзер пробует ещё раз правильно → проверили
  LLM-вызовом.
- **Частота/графики** — сортировка по частоте не требует новых данных
  (просто `GROUP BY category ORDER BY count DESC`), но настоящие
  графики имеют смысл именно в Mini App с полноценным UI, а не в
  Telegram-чате текстовыми блоками — переносить это в чат до Mini App
  не стоит.

### 2. Peer practice — ученики общаются друг с другом

Mrs. Smith в разговоре сама спросила: "How do you plan to expand the
features so learners can connect with each other while they
practice?" — то есть предложила какую-то форму общения между
пользователями бота во время практики, не только с персонажами.

Пока это сырая идея без проработки — не зафиксировано даже направление
(парная практика вживую, обмен сообщениями, что-то ещё). Требует
отдельного обсуждения, прежде чем превращать в конкретный план:
конфликтует ли это с приватностью текущей модели (один юзер — один
приватный чат с ботом), какая модерация нужна, как это сочетается с
персонажами. Не в работе, просто зафиксировано, чтобы не потерялось.

---

## Общий ToDo-лист проекта

### В работе / известные баги

1. **Mrs. Smith / Mark recasting** — сейчас может прямо указывать на
   ошибку («you might mean...») вместо органичного использования
   правильной формы в ответе
2. **Recasting целой фразы** — иногда выделяет жирным слишком много
   текста вместо только исправленного слова
3. Общий пересмотр Tutor Mode — см. раздел выше

### Реализовано (последняя крупная сессия)

- ✅ Онбординг v2 — имя пользователя, предложение английского варианта
  имени через LLM (Илья → Elijah) с подтверждением, выбор цели обучения
- ✅ Synonym Streak — отслеживание повторяющейся лексики в Tutor Mode
  с Mrs. Smith, предложение синонимов при 3+ повторах слова
- ✅ Session Summary — PDF с полным транскриптом сессии + LLM анализ
  ошибок и прогресса, генерируется при исчерпании дневного лимита
  сообщений
- ✅ Telegram Stars — paywall без ЕРИП (юрисдикция РБ создаёт отдельные
  сложности с хостингом и платёжным шлюзом — отложено), тарифы
  Standard/Pro с периодами неделя/месяц
- ✅ Миграция модели — Llama 4 Scout и Llama 3.1 8B Instant были
  deprecated Groq'ом, переехали на `openai/gpt-oss-120b`
- ✅ Ревью репозитория (2026-08-31) — найдено и исправлено 8 багов:
  пустые re-engagement сообщения, молчание бота при сбое TTS в
  Tutor/Flow Mode, сломанный HTML в Deep Dive, потерянный
  corrected_text в Sunday Deep Dive, несуществующий уровень
  "Elementary", мёртвая кнопка PenFriend при лимите, обход paywall на
  персонажей, никогда не истекающие подписки. Плюс улучшена надёжность:
  `/health` теперь реально проверяет Supabase, Sunday Deep Dive
  устойчивее к рестартам процесса. См. `TUTOR_MODE_SPEC_NEXT.md` для
  ближайшей итерации Tutor Mode (session-память об ошибках + усиленный
  Session Summary).

### Запланировано, не начато

4. **Drop-in Talks** — сценарные разговоры («At the doctor's», «Job
   interview» и т.п.) на существующих персонажах, голос подбирается под
   сценарий; кастомный сценарий пользователя — с ручным выбором голоса
5. **ЕРИП** — оплата картой для пользователей в РБ; требует юрлицо/ИП,
   белорусский хостинг для сервера и БД (Render/Supabase не подходят
   по юрисдикции) — отложено до появления юридической стороны
6. **Vocabulary/Mistakes экран (Mini App)** — см. раздел выше, не скоро

### Обсуждено, отклонено

- **Word Focus** (целевое слово в разговоре) — отменено в пользу
  Synonym Streak как более универсальной и полезной механики
- **Session Summary → Telegraph** — красивая идея с личной страницей
  сессий на telegra.ph, в итоге выбран более простой путь — PDF-экспорт
- **Персонаж с синдромом Туретта** — обсуждалось как комедийная идея,
  не более чем идея на будущее для Drop-in Talks
- **Конвертация бота в PWA** — обсуждалось неформально; вывод: смысла
  сейчас нет, Telegram даёт готовую инфраструктуру и аудиторию; live
  chat со streaming — потенциальная killer-feature для будущей PWA
  версии, но не приоритет

### Идеи для дальнейшего изучения (из анализа конкурента Fluently)

- Хороший референс онбординга: имя, цель обучения, сфера работы —
  частично уже взято (имя, цель)
- Анализ акцента как маркетинговый крючок — решено не делать, честно
  оценить акцент по аудио нельзя, будет просто красивой подделкой
