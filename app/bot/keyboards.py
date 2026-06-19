"""Inline keyboards for the bot UI (localized)."""

from __future__ import annotations

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
from aiogram.utils.keyboard import InlineKeyboardBuilder

from app.bot.i18n import LANGUAGE_NAMES, SUPPORTED_LANGS, t

COUNT_PRESETS = (50, 100, 250, 500, 1000)


def main_menu(lang: str) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(text=t("btn_buy", lang), callback_data="buy:start")
    builder.button(text=t("btn_orders", lang), callback_data="orders:list")
    builder.button(text=t("btn_help", lang), callback_data="help:show")
    builder.button(text=t("btn_language", lang), callback_data="lang:choose")
    builder.adjust(1)
    return builder.as_markup()


def count_presets(lang: str) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    for preset in COUNT_PRESETS:
        builder.button(text=f"{preset} ⭐", callback_data=f"count:{preset}")
    builder.button(text=t("btn_custom", lang), callback_data="count:custom")
    builder.button(text=t("btn_cancel", lang), callback_data="buy:cancel")
    builder.adjust(2, 2, 1, 1)
    return builder.as_markup()


def confirm_payment(lang: str) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(text=t("btn_pay", lang), callback_data="pay:confirm")
    builder.button(text=t("btn_cancel", lang), callback_data="buy:cancel")
    builder.adjust(1)
    return builder.as_markup()


def payment_link(url: str, order_id: str, lang: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text=t("btn_goto_pay", lang), url=url)],
            [InlineKeyboardButton(text=t("btn_check", lang), callback_data=f"check:{order_id}")],
        ]
    )


def retry_buy(lang: str) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(text=t("btn_retry", lang), callback_data="buy:start")
    builder.button(text=t("btn_menu", lang), callback_data="menu:show")
    builder.adjust(1)
    return builder.as_markup()


def language_picker() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    for code in SUPPORTED_LANGS:
        builder.button(text=LANGUAGE_NAMES[code], callback_data=f"lang:set:{code}")
    builder.adjust(1)
    return builder.as_markup()
