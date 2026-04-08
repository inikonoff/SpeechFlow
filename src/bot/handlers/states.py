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
    waiting_level = State()       # показали приветствие, ждём выбор уровня
    choosing_mode = State()       # показали голосовое уровня, ждём выбор режима


class LevelChangeState(StatesGroup):
    choosing_level = State()   # пользователь нажал "Сменить уровень"
