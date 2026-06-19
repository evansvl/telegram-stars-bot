"""Inline keyboards for the bot UI (localized)."""

from __future__ import annotations

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
from aiogram.utils.keyboard import InlineKeyboardBuilder

from app.bot.i18n import LANGUAGE_NAMES, SUPPORTED_LANGS, t
from app.config import get_settings

COUNT_PRESETS = (50, 100, 250, 500, 1000)


def main_menu(lang: str) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(text=t("btn_buy", lang), callback_data="buy:start")
    builder.button(text=t("btn_orders", lang), callback_data="orders:list")
    builder.button(text=t("btn_referral", lang), callback_data="ref:show")
    builder.button(text=t("btn_help", lang), callback_data="help:show")
    builder.button(text=t("btn_language", lang), callback_data="lang:choose")
    settings = get_settings()
    if settings.terms_url:
        builder.button(text=t("btn_terms", lang), url=settings.terms_url)
    if settings.privacy_url:
        builder.button(text=t("btn_privacy", lang), url=settings.privacy_url)
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


def test_payment(order_id: str, lang: str) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(text=t("btn_test_pay", lang), callback_data=f"testpay:{order_id}")
    builder.button(text=t("btn_menu", lang), callback_data="menu:show")
    builder.adjust(1)
    return builder.as_markup()


def retry_buy(lang: str) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(text=t("btn_retry", lang), callback_data="buy:start")
    builder.button(text=t("btn_menu", lang), callback_data="menu:show")
    builder.adjust(1)
    return builder.as_markup()


def referral_menu(lang: str, *, can_withdraw: bool) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    if can_withdraw:
        builder.button(text=t("btn_withdraw", lang), callback_data="wd:start")
    builder.button(text=t("btn_withdrawals", lang), callback_data="wd:list")
    builder.button(text=t("btn_menu", lang), callback_data="menu:show")
    builder.adjust(1)
    return builder.as_markup()


def withdraw_methods(lang: str) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(text=t("btn_method_sbp", lang), callback_data="wd:method:sbp")
    builder.button(text=t("btn_method_crypto", lang), callback_data="wd:method:crypto")
    builder.button(text=t("btn_cancel", lang), callback_data="wd:cancel")
    builder.adjust(1)
    return builder.as_markup()


def withdraw_cancel(lang: str) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(text=t("btn_cancel", lang), callback_data="wd:cancel")
    return builder.as_markup()


def cancel_button(lang: str, data: str = "buy:cancel") -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(text=t("btn_cancel", lang), callback_data=data)
    return builder.as_markup()


def admin_withdrawal_actions(withdrawal_id: int, lang: str) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(text=t("btn_approve", lang), callback_data=f"wda:approve:{withdrawal_id}")
    builder.button(text=t("btn_reject", lang), callback_data=f"wda:reject:{withdrawal_id}")
    builder.adjust(2)
    return builder.as_markup()


def language_picker() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    for code in SUPPORTED_LANGS:
        builder.button(text=LANGUAGE_NAMES[code], callback_data=f"lang:set:{code}")
    builder.adjust(1)
    return builder.as_markup()
