"""
SpeechFlow AI — Modes
Three modes, three different contracts with the user.
"""

# ─── Mode constants ────────────────────────────────────────────────────────

MODE_TUTOR = "tutor"
MODE_PENFRIEND = "penfriend"
MODE_FLOW = "flow"

# ─── Mode descriptions (for onboarding and menu) ──────────────────────────

MODE_DESCRIPTIONS = {
    MODE_TUTOR: (
        "🎓 <b>Tutor Mode</b>\n"
        "Send voice or text messages in English. "
        "The bot listens, corrects your mistakes, and explains them. "
        "Best for focused practice."
    ),
    MODE_PENFRIEND: (
        "✉️ <b>PenFriend Mode</b>\n"
        "Text chat with one of six characters. "
        "The bot gently models correct English without breaking the conversation. "
        "Best for natural, low-pressure practice."
    ),
    MODE_FLOW: (
        "🎙 <b>Flow Mode</b>\n"
        "Pure voice conversation with a character. "
        "No corrections, no analysis — just real talk. "
        "Best for fluency and confidence."
    ),
}

# ─── Correction rate presets ──────────────────────────────────────────────

CORRECTION_RATE_RELAXED  = 20   # ~1 in 5 errors
CORRECTION_RATE_BALANCED = 50   # ~1 in 2 errors
CORRECTION_RATE_STRICT   = 80   # ~4 in 5 errors

CORRECTION_RATE_DEFAULT = CORRECTION_RATE_BALANCED

CORRECTION_RATE_LABELS = {
    CORRECTION_RATE_RELAXED:  "😌 Relaxed",
    CORRECTION_RATE_BALANCED: "⚖️ Balanced",
    CORRECTION_RATE_STRICT:   "🎯 Strict — Душнила",
}

def correction_rate_label(rate: int) -> str:
    return CORRECTION_RATE_LABELS.get(rate, f"⚖️ {rate}%")

def correction_rate_instruction(rate: int) -> str:
    """Translates numeric correction rate into a prompt instruction for correct_text."""
    if rate <= 20:
        return (
            "RELAXED mode: Only flag errors that would cause a native speaker to misunderstand the message entirely. "
            "Ignore articles (a/an/the), minor preposition slips, word order issues, small grammar mistakes. "
            "If the message is understandable — return error_category: none."
        )
    elif rate <= 50:
        return (
            "BALANCED mode: Find the single most important grammatical error in the message. "
            "Ignore articles and minor slips unless they are the only issue. "
            "If the message has no real errors — return error_category: none."
        )
    else:
        return (
            "STRICT mode: Find the single most important error — prioritise grammar, prepositions, articles, word order. "
            "Even minor errors like missing 'the' or wrong preposition count. "
            "Always return the most impactful correction, never more than one."
        )


# ─── Промпт для режима Tutor ──────────────────────────────────────────────

TUTOR_SYSTEM_PROMPT = """You are Speech Flow AI, an English conversation tutor.
Your job is to have a natural conversation while helping the user improve their English.

# RULES
- Respond naturally to what the user said
- NEVER explicitly say "you made an error" or "that was wrong"
- Use correct English naturally in your response
- Keep responses under 80 words
- End with ONE question to keep the conversation going
- Never start with "That's interesting", "Great", "I see", "Cool"
- Use natural reactions: "Wait, really?", "Huh.", "Yeah that tracks", "No way"
"""


# ─── Промпт для режима PenFriend ──────────────────────────────────────────

def get_penfriend_system_prompt(
    persona_prompt: str,
    correction_rate: int = CORRECTION_RATE_DEFAULT,
    session_errors: list = None
) -> str:
    """
    Builds the full system prompt for PenFriend mode.

    Correction happens in two natural ways:
    1. IMPLICIT: bot uses the correct form in its own response without comment.
       Example: user writes "I goed to store" -> bot replies "oh you went there, how was it?"

    2. SOFT CLARIFICATION: bot gently mirrors the corrected form as a question.
       Example: "You mean you have been working on this since Monday? [continues naturally]"

    Use implicit correction more often.
    Soft clarification only for more impactful errors.
    Never correct the same error twice. Never correct more than one error per message.
    """

    correction_instruction = correction_rate_instruction(correction_rate)

    errors_block = ""
    if session_errors:
        formatted = ", ".join(session_errors[-5:])  # last 5 only
        errors_block = (
            "\n\n# SESSION ERRORS TO NATURALLY REINFORCE\n"
            f"The user made these errors earlier in this conversation: {formatted}. "
            "If a natural opportunity arises, model the correct form in your own speech. "
            "Do not force it. Do not repeat the same correction twice."
        )

    return (
        f"{persona_prompt}\n\n"
        "# PENFRIEND MODE\n"
        "This is a text-based conversation. No voice. No lesson. Just a natural exchange.\n"
        "You are a real person having a real conversation — not a teacher.\n\n"
        "# RECASTING (correction approach)\n"
        f"{correction_instruction}\n\n"
        "If the user made a grammatical error, use the corrected form naturally in your own response "
        "and wrap ONLY the corrected part in <b>...</b> HTML tags.\n"
        "Example: user writes 'I looking forward to see you' → "
        "you reply 'I am <b>looking forward to seeing</b> you too! ...'\n"
        "NEVER say 'you made a mistake', 'that is wrong', or anything that sounds like correction.\n"
        "NEVER wrap your entire response in bold — only the corrected fragment.\n"
        "If no error — reply normally, no bold needed."
        f"{errors_block}"
    )
