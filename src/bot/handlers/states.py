"""
SpeechFlow Pro — FSM States
Shared across handlers to avoid circular imports.
"""

from aiogram.fsm.state import State, StatesGroup


class FlowState(StatesGroup):
    choosing_persona = State()
    active = State()


class TutorState(StatesGroup):
    awaiting_drill = State()


class AdminState(StatesGroup):
    waiting_broadcast = State()
