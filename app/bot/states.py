"""FSM states for the buy flow."""

from __future__ import annotations

from aiogram.fsm.state import State, StatesGroup


class BuyStates(StatesGroup):
    waiting_username = State()
    waiting_count = State()
    waiting_custom_count = State()
    confirm_payment = State()


class WithdrawStates(StatesGroup):
    choosing_method = State()
    entering_destination = State()
    entering_amount = State()


class AdminWithdrawStates(StatesGroup):
    waiting_proof = State()
    waiting_reject_reason = State()


class PartnerStates(StatesGroup):
    entering_markup = State()
