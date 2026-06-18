"""Inline keyboards for the bot UI."""

from __future__ import annotations

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
from aiogram.utils.keyboard import InlineKeyboardBuilder

COUNT_PRESETS = (50, 100, 250, 500, 1000)


def main_menu() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(text="⭐ Купить звёзды", callback_data="buy:start")
    builder.button(text="🧾 Мои заказы", callback_data="orders:list")
    builder.button(text="❓ Помощь", callback_data="help:show")
    builder.adjust(1)
    return builder.as_markup()


def count_presets() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    for preset in COUNT_PRESETS:
        builder.button(text=f"{preset} ⭐", callback_data=f"count:{preset}")
    builder.button(text="✏️ Ввести своё", callback_data="count:custom")
    builder.button(text="✖️ Отмена", callback_data="buy:cancel")
    builder.adjust(2, 2, 1, 1)
    return builder.as_markup()


def confirm_payment() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(text="💳 Оплатить", callback_data="pay:confirm")
    builder.button(text="✖️ Отмена", callback_data="buy:cancel")
    builder.adjust(1)
    return builder.as_markup()


def payment_link(url: str, order_id: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="💳 Перейти к оплате", url=url)],
            [InlineKeyboardButton(text="🔄 Проверить оплату", callback_data=f"check:{order_id}")],
        ]
    )


def retry_buy() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(text="🔁 Попробовать снова", callback_data="buy:start")
    builder.button(text="🏠 В меню", callback_data="menu:show")
    builder.adjust(1)
    return builder.as_markup()
