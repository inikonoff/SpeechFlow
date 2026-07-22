# CHANGELOG: 2026-07-16
# - OnboardingState: добавлены waiting_name, confirming_english_name, choosing_goal
# - PaywallState: новые состояния для Telegram Stars

"""
SpeechFlow Pro — FSM States
Shared across handlers to avoid circular imports.
"""

from aiogram.fsm.state import State, StatesGroup


class FlowState(StatesGroup):
    choosing_persona = State()
    active = State()


class AdminState(StatesGroup):
    waiting_broadcast = State()


class TutorState(StatesGroup):
    awaiting_drill = State()   # ждём повтор после коррекции


class OnboardingState(StatesGroup):
    waiting_name            = State()  # просим ввести имя
    confirming_english_name = State()  # предлагаем английский вариант имени
    choosing_goal           = State()  # выбор цели обучения
    waiting_level           = State()  # выбор уровня
    choosing_mode           = State()  # выбор режима


class LevelChangeState(StatesGroup):
    choosing_level = State()


class PaywallState(StatesGroup):
    choosing_plan = State()   # пользователь выбирает тариф
