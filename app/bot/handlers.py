"""aiogram handlers implementing the buy flow, history, help and admin stats."""

from __future__ import annotations

import logging
import re
from decimal import Decimal

from aiogram import F, Router
from aiogram.filters import Command, CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message

from app.bot import keyboards
from app.bot.i18n import normalize_lang, status_label, t
from app.bot.states import BuyStates
from app.config import Settings
from app.pricing import MAX_STARS, MIN_STARS, compute_amount, is_valid_count
from app.services import OrderService
from app.wata.errors import WataError

logger = logging.getLogger(__name__)
router = Router(name="main")

_USERNAME_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_]{4,31}$")


def _format_quote(target: str, count: int, amount: Decimal, lang: str) -> str:
    return t("quote", lang, target=target, count=count, amount=amount)


# ── Entry points ─────────────────────────────────────────────


@router.message(CommandStart())
async def cmd_start(message: Message, state: FSMContext, lang: str) -> None:
    await state.clear()
    await message.answer(t("welcome", lang), reply_markup=keyboards.main_menu(lang))


@router.message(Command("help"))
async def cmd_help(message: Message, lang: str) -> None:
    await message.answer(t("help", lang), reply_markup=keyboards.main_menu(lang))


@router.callback_query(F.data == "help:show")
async def cb_help(call: CallbackQuery, lang: str) -> None:
    if isinstance(call.message, Message):
        await call.message.answer(t("help", lang), reply_markup=keyboards.main_menu(lang))
    await call.answer()


@router.callback_query(F.data == "menu:show")
async def cb_menu(call: CallbackQuery, state: FSMContext, lang: str) -> None:
    await state.clear()
    if isinstance(call.message, Message):
        await call.message.answer(t("welcome", lang), reply_markup=keyboards.main_menu(lang))
    await call.answer()


@router.message(Command("cancel"))
async def cmd_cancel(message: Message, state: FSMContext, lang: str) -> None:
    await state.clear()
    await message.answer(t("cancelled_menu", lang), reply_markup=keyboards.main_menu(lang))


@router.callback_query(F.data == "buy:cancel")
async def cb_cancel(call: CallbackQuery, state: FSMContext, lang: str) -> None:
    await state.clear()
    if isinstance(call.message, Message):
        await call.message.answer(t("cancelled", lang), reply_markup=keyboards.main_menu(lang))
    await call.answer()


# ── Language ─────────────────────────────────────────────────


@router.message(Command("language"))
async def cmd_language(message: Message, lang: str) -> None:
    await message.answer(t("choose_language", lang), reply_markup=keyboards.language_picker())


@router.callback_query(F.data == "lang:choose")
async def cb_language_choose(call: CallbackQuery, lang: str) -> None:
    if isinstance(call.message, Message):
        await call.message.answer(
            t("choose_language", lang), reply_markup=keyboards.language_picker()
        )
    await call.answer()


@router.callback_query(F.data.startswith("lang:set:"))
async def cb_language_set(call: CallbackQuery, service: OrderService) -> None:
    chosen = normalize_lang((call.data or "").split(":")[-1])
    if call.from_user:
        await service.set_language(call.from_user.id, chosen)
    if isinstance(call.message, Message):
        await call.message.answer(
            t("language_set", chosen), reply_markup=keyboards.main_menu(chosen)
        )
    await call.answer()


# ── Buy flow ─────────────────────────────────────────────────


@router.callback_query(F.data == "buy:start")
async def cb_buy_start(call: CallbackQuery, state: FSMContext, lang: str) -> None:
    await state.clear()
    await state.set_state(BuyStates.waiting_username)
    if isinstance(call.message, Message):
        await call.message.answer(t("ask_username", lang))
    await call.answer()


@router.message(BuyStates.waiting_username, F.text)
async def on_username(
    message: Message, state: FSMContext, service: OrderService, lang: str
) -> None:
    username = (message.text or "").strip().lstrip("@")
    if not _USERNAME_RE.match(username):
        await message.answer(t("username_invalid", lang))
        return

    try:
        price = await service.get_star_price(username)
    except WataError as exc:
        await message.answer(
            f"⚠️ {t(exc.message_key(), lang)}\n\n{t('enter_other_username', lang)}"
        )
        return

    await state.update_data(target_username=username, min_price=str(price.min_price))
    await state.set_state(BuyStates.waiting_count)
    await message.answer(
        t("user_found", lang, username=username), reply_markup=keyboards.count_presets(lang)
    )


async def _show_quote(
    message: Message, state: FSMContext, service: OrderService, count: int, lang: str
) -> None:
    data = await state.get_data()
    target = data.get("target_username")
    min_price_raw = data.get("min_price")
    if not target or min_price_raw is None:
        await message.answer(t("session_expired", lang))
        await state.clear()
        return

    quote = compute_amount(Decimal(str(min_price_raw)), count, service.markup_percent)
    await state.update_data(count=count, amount=str(quote.amount))
    await state.set_state(BuyStates.confirm_payment)
    await message.answer(
        _format_quote(target, count, quote.amount, lang),
        reply_markup=keyboards.confirm_payment(lang),
    )


@router.callback_query(BuyStates.waiting_count, F.data.startswith("count:"))
async def cb_count(
    call: CallbackQuery, state: FSMContext, service: OrderService, lang: str
) -> None:
    value = (call.data or "").split(":", 1)[1]
    if value == "custom":
        await state.set_state(BuyStates.waiting_custom_count)
        if isinstance(call.message, Message):
            await call.message.answer(t("ask_custom_count", lang, min=MIN_STARS, max=MAX_STARS))
        await call.answer()
        return

    count = int(value)
    if isinstance(call.message, Message):
        await _show_quote(call.message, state, service, count, lang)
    await call.answer()


@router.message(BuyStates.waiting_custom_count, F.text)
async def on_custom_count(
    message: Message, state: FSMContext, service: OrderService, lang: str
) -> None:
    raw = (message.text or "").strip().replace(" ", "")
    if not raw.isdigit():
        await message.answer(t("not_integer", lang))
        return
    count = int(raw)
    if not is_valid_count(count):
        await message.answer(t("count_out_of_range", lang, min=MIN_STARS, max=MAX_STARS))
        return
    await _show_quote(message, state, service, count, lang)


@router.callback_query(BuyStates.confirm_payment, F.data == "pay:confirm")
async def cb_pay(call: CallbackQuery, state: FSMContext, service: OrderService, lang: str) -> None:
    data = await state.get_data()
    target = data.get("target_username")
    count = data.get("count")
    amount = data.get("amount")
    if not (target and count and amount) or not isinstance(call.message, Message):
        await call.answer(t("session_expired_alert", lang), show_alert=True)
        await state.clear()
        return

    await call.answer(t("creating_order", lang))
    user = call.from_user
    try:
        created = await service.create_order(
            buyer_tg_id=user.id,
            buyer_username=user.username,
            target_username=str(target),
            count=int(count),
            amount=Decimal(str(amount)),
        )
    except WataError as exc:
        await call.message.answer(
            f"⚠️ {t(exc.message_key(), lang)}", reply_markup=keyboards.retry_buy(lang)
        )
        await state.clear()
        return

    await state.clear()
    await call.message.answer(
        t(
            "order_created",
            lang,
            count=created.count,
            target=created.target_username,
            amount=created.amount,
        ),
        reply_markup=keyboards.payment_link(created.payment_link, created.order_id, lang),
    )


@router.callback_query(F.data.startswith("check:"))
async def cb_check(call: CallbackQuery, service: OrderService, lang: str) -> None:
    order_id = (call.data or "").split(":", 1)[1]
    await call.answer(t("checking_status", lang))
    order = await service.sync_order(order_id)
    if order is None or not isinstance(call.message, Message):
        if isinstance(call.message, Message):
            await call.message.answer(t("order_not_found", lang))
        return
    await call.message.answer(t("order_status", lang, label=status_label(order.status, lang)))


# ── History & admin ──────────────────────────────────────────


@router.message(Command("orders"))
async def cmd_orders(message: Message, service: OrderService, lang: str) -> None:
    tg_id = message.from_user.id if message.from_user else 0
    await _send_orders(message, service, tg_id, lang)


@router.callback_query(F.data == "orders:list")
async def cb_orders(call: CallbackQuery, service: OrderService, lang: str) -> None:
    if isinstance(call.message, Message) and call.from_user:
        await _send_orders(call.message, service, call.from_user.id, lang)
    await call.answer()


async def _send_orders(message: Message, service: OrderService, tg_id: int, lang: str) -> None:
    orders = await service.list_orders(tg_id)
    if not orders:
        await message.answer(t("orders_empty", lang), reply_markup=keyboards.main_menu(lang))
        return
    lines = [t("orders_header", lang)]
    for o in orders:
        lines.append(
            t(
                "order_line",
                lang,
                count=o.count,
                target=o.target_username,
                amount=o.amount,
                status=status_label(o.status, lang),
            )
        )
    await message.answer("\n".join(lines), reply_markup=keyboards.main_menu(lang))


@router.message(Command("stats"))
async def cmd_stats(message: Message, service: OrderService, settings: Settings, lang: str) -> None:
    if not message.from_user or not settings.is_admin(message.from_user.id):
        await message.answer(t("admin_only", lang))
        return
    stats = await service.stats()
    await message.answer(
        t(
            "stats",
            lang,
            total=stats["total_orders"],
            paid=stats["paid_orders"],
            turnover=stats["turnover"],
            margin=stats["margin"],
        )
    )


@router.message(BuyStates.waiting_username)
@router.message(BuyStates.waiting_custom_count)
async def on_unexpected(message: Message, lang: str) -> None:
    await message.answer(t("send_text_or_cancel", lang))
