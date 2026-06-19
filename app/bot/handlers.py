"""aiogram handlers. Button-driven UI that edits one message in place.

To avoid cluttering the chat, navigation edits the existing message instead of
sending new ones (falling back to delete+send when an edit isn't possible). The
only slash command is /start (also the referral deep-link target and the native
"Menu" button); everything else is reachable via inline buttons. /stats stays as
a hidden admin-only command.
"""

from __future__ import annotations

import logging
import re
from contextlib import suppress
from decimal import Decimal, InvalidOperation

from aiogram import F, Router
from aiogram.exceptions import TelegramBadRequest
from aiogram.filters import Command, CommandObject, CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, InlineKeyboardMarkup, Message

from app.bot import keyboards
from app.bot.i18n import normalize_lang, status_label, t, withdrawal_status_label
from app.bot.states import AdminWithdrawStates, BuyStates, WithdrawStates
from app.config import Settings
from app.pricing import MAX_STARS, MIN_STARS, compute_amount, is_valid_count
from app.services import OrderService, ReferralService, WithdrawalError
from app.wata.errors import WataError

logger = logging.getLogger(__name__)
router = Router(name="main")

_USERNAME_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_]{4,31}$")


async def _render(
    event: Message | CallbackQuery,
    text: str,
    reply_markup: InlineKeyboardMarkup | None = None,
) -> None:
    """Edit the message in place on callbacks; send a fresh one on messages.

    Keeps the chat to a single, mutating "anchor" message during navigation.
    Falls back to delete+send if the message can't be edited.
    """
    if isinstance(event, CallbackQuery):
        msg = event.message
        if not isinstance(msg, Message):
            return
        try:
            await msg.edit_text(text, reply_markup=reply_markup)
        except TelegramBadRequest as exc:
            if "not modified" in str(exc).lower():
                return
            with suppress(TelegramBadRequest):
                await msg.delete()
            await msg.answer(text, reply_markup=reply_markup)
        return
    await event.answer(text, reply_markup=reply_markup)


# ── Entry points ─────────────────────────────────────────────


@router.message(CommandStart())
async def cmd_start(
    message: Message,
    state: FSMContext,
    command: CommandObject,
    referral: ReferralService,
    lang: str,
) -> None:
    await state.clear()
    payload = (command.args or "").strip()
    if payload.startswith("ref_") and message.from_user:
        try:
            referrer_id = int(payload[4:])
        except ValueError:
            referrer_id = 0
        if referrer_id:
            await referral.register_referral(message.from_user.id, referrer_id)
    await message.answer(t("welcome", lang), reply_markup=keyboards.main_menu(lang))


@router.callback_query(F.data == "help:show")
async def cb_help(call: CallbackQuery, lang: str) -> None:
    await _render(call, t("help", lang), keyboards.main_menu(lang))
    await call.answer()


@router.callback_query(F.data == "menu:show")
async def cb_menu(call: CallbackQuery, state: FSMContext, lang: str) -> None:
    await state.clear()
    await _render(call, t("welcome", lang), keyboards.main_menu(lang))
    await call.answer()


@router.callback_query(F.data == "buy:cancel")
async def cb_cancel(call: CallbackQuery, state: FSMContext, lang: str) -> None:
    await state.clear()
    await _render(call, t("welcome", lang), keyboards.main_menu(lang))
    await call.answer()


# ── Language ─────────────────────────────────────────────────


@router.callback_query(F.data == "lang:choose")
async def cb_language_choose(call: CallbackQuery, lang: str) -> None:
    await _render(call, t("choose_language", lang), keyboards.language_picker())
    await call.answer()


@router.callback_query(F.data.startswith("lang:set:"))
async def cb_language_set(call: CallbackQuery, service: OrderService) -> None:
    chosen = normalize_lang((call.data or "").split(":")[-1])
    if call.from_user:
        await service.set_language(call.from_user.id, chosen)
    await _render(call, t("language_set", chosen), keyboards.main_menu(chosen))
    await call.answer()


# ── Buy flow ─────────────────────────────────────────────────


@router.callback_query(F.data == "buy:start")
async def cb_buy_start(call: CallbackQuery, state: FSMContext, lang: str) -> None:
    await state.clear()
    await state.set_state(BuyStates.waiting_username)
    await _render(call, t("ask_username", lang), keyboards.cancel_button(lang))
    await call.answer()


@router.message(BuyStates.waiting_username, F.text)
async def on_username(
    message: Message, state: FSMContext, service: OrderService, lang: str
) -> None:
    username = (message.text or "").strip().lstrip("@")
    if not _USERNAME_RE.match(username):
        await message.answer(
            t("username_invalid", lang), reply_markup=keyboards.cancel_button(lang)
        )
        return

    try:
        price = await service.get_star_price(username)
    except WataError as exc:
        await message.answer(
            f"⚠️ {t(exc.message_key(), lang)}\n\n{t('enter_other_username', lang)}",
            reply_markup=keyboards.cancel_button(lang),
        )
        return

    await state.update_data(target_username=username, min_price=str(price.min_price))
    await state.set_state(BuyStates.waiting_count)
    await message.answer(
        t("user_found", lang, username=username), reply_markup=keyboards.count_presets(lang)
    )


async def _show_quote(
    event: Message | CallbackQuery,
    state: FSMContext,
    service: OrderService,
    count: int,
    lang: str,
) -> None:
    data = await state.get_data()
    target = data.get("target_username")
    min_price_raw = data.get("min_price")
    if not target or min_price_raw is None:
        await _render(event, t("session_expired", lang), keyboards.main_menu(lang))
        await state.clear()
        return

    quote = compute_amount(Decimal(str(min_price_raw)), count, service.markup_percent)
    await state.update_data(count=count, amount=str(quote.amount))
    await state.set_state(BuyStates.confirm_payment)
    await _render(
        event,
        t("quote", lang, target=target, count=count, amount=quote.amount),
        keyboards.confirm_payment(lang),
    )


@router.callback_query(BuyStates.waiting_count, F.data.startswith("count:"))
async def cb_count(
    call: CallbackQuery, state: FSMContext, service: OrderService, lang: str
) -> None:
    value = (call.data or "").split(":", 1)[1]
    if value == "custom":
        await state.set_state(BuyStates.waiting_custom_count)
        await _render(
            call, t("ask_custom_count", lang, min=MIN_STARS, max=MAX_STARS),
            keyboards.cancel_button(lang),
        )
        await call.answer()
        return

    await _show_quote(call, state, service, int(value), lang)
    await call.answer()


@router.message(BuyStates.waiting_custom_count, F.text)
async def on_custom_count(
    message: Message, state: FSMContext, service: OrderService, lang: str
) -> None:
    raw = (message.text or "").strip().replace(" ", "")
    if not raw.isdigit():
        await message.answer(t("not_integer", lang), reply_markup=keyboards.cancel_button(lang))
        return
    count = int(raw)
    if not is_valid_count(count):
        await message.answer(
            t("count_out_of_range", lang, min=MIN_STARS, max=MAX_STARS),
            reply_markup=keyboards.cancel_button(lang),
        )
        return
    await _show_quote(message, state, service, count, lang)


@router.callback_query(BuyStates.confirm_payment, F.data == "pay:confirm")
async def cb_pay(call: CallbackQuery, state: FSMContext, service: OrderService, lang: str) -> None:
    data = await state.get_data()
    target = data.get("target_username")
    count = data.get("count")
    amount = data.get("amount")
    if not (target and count and amount) or not call.from_user:
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
        await _render(call, f"⚠️ {t(exc.message_key(), lang)}", keyboards.retry_buy(lang))
        await state.clear()
        return

    await state.clear()
    if created.test:
        await _render(
            call,
            t(
                "test_order_created",
                lang,
                count=created.count,
                target=created.target_username,
                amount=created.amount,
            ),
            keyboards.test_payment(created.order_id, lang),
        )
        return
    await _render(
        call,
        t(
            "order_created",
            lang,
            count=created.count,
            target=created.target_username,
            amount=created.amount,
        ),
        keyboards.payment_link(created.payment_link, created.order_id, lang),
    )


@router.callback_query(F.data.startswith("testpay:"))
async def cb_testpay(
    call: CallbackQuery, service: OrderService, referral: ReferralService, lang: str
) -> None:
    """Simulate a successful payment (test mode) and run the payout flow."""
    order_id = (call.data or "").split(":", 1)[1]
    order = await service.simulate_payment(order_id)
    await call.answer()
    if order is None:
        return
    await _render(call, t("test_paid_done", lang), keyboards.main_menu(lang))
    credited = await referral.credit_for_order(order)
    if credited:
        referrer_id, reward = credited
        rlang = await service.get_user_language(referrer_id)
        try:
            await call.bot.send_message(
                referrer_id, t("referral_earned_notify", rlang, amount=reward)
            )
        except Exception:
            logger.exception("failed to notify referrer tg_id=%s", referrer_id)


@router.callback_query(F.data.startswith("check:"))
async def cb_check(
    call: CallbackQuery, service: OrderService, referral: ReferralService, lang: str
) -> None:
    order_id = (call.data or "").split(":", 1)[1]
    await call.answer(t("checking_status", lang))
    order = await service.sync_order(order_id)
    if order is None:
        return
    # Credit the referrer if this manual check is what confirms the payment.
    await referral.credit_for_order(order)
    if isinstance(call.message, Message):
        await call.message.answer(t("order_status", lang, label=status_label(order.status, lang)))


# ── Referral program ─────────────────────────────────────────


def _fmt_percent(value: float) -> str:
    return str(int(value)) if value == int(value) else str(value)


@router.callback_query(F.data == "ref:show")
async def cb_referral(call: CallbackQuery, referral: ReferralService, lang: str) -> None:
    if not call.from_user:
        await call.answer()
        return
    ov = await referral.overview(call.from_user.id)
    text = t(
        "referral_overview",
        lang,
        percent=_fmt_percent(referral.percent),
        link=ov.link,
        referrals=ov.referrals,
        earned=ov.earned,
        available=ov.available,
    )
    can_withdraw = ov.available > 0 and not ov.has_pending
    await _render(call, text, keyboards.referral_menu(lang, can_withdraw=can_withdraw))
    await call.answer()


# ── Withdrawals (user) ───────────────────────────────────────


@router.callback_query(F.data == "wd:start")
async def cb_withdraw_start(
    call: CallbackQuery, state: FSMContext, referral: ReferralService, lang: str
) -> None:
    if not call.from_user:
        await call.answer()
        return
    ov = await referral.overview(call.from_user.id)
    if ov.has_pending:
        await call.answer(t("wd_has_pending", lang), show_alert=True)
        return
    if ov.available <= 0:
        await call.answer(t("wd_over_balance", lang, available=ov.available), show_alert=True)
        return
    await state.set_state(WithdrawStates.choosing_method)
    await _render(call, t("withdraw_choose_method", lang), keyboards.withdraw_methods(lang))
    await call.answer()


@router.callback_query(WithdrawStates.choosing_method, F.data.startswith("wd:method:"))
async def cb_withdraw_method(call: CallbackQuery, state: FSMContext, lang: str) -> None:
    method = (call.data or "").split(":")[-1]
    await state.update_data(method=method)
    await state.set_state(WithdrawStates.entering_destination)
    key = (
        "withdraw_ask_destination_crypto"
        if method == "crypto"
        else "withdraw_ask_destination_sbp"
    )
    await _render(call, t(key, lang), keyboards.withdraw_cancel(lang))
    await call.answer()


@router.message(WithdrawStates.entering_destination, F.text)
async def on_withdraw_destination(
    message: Message, state: FSMContext, referral: ReferralService, lang: str
) -> None:
    destination = (message.text or "").strip()
    if not destination:
        return
    data = await state.get_data()
    method = str(data.get("method", "sbp"))
    await state.update_data(destination=destination)
    await state.set_state(WithdrawStates.entering_amount)
    tg_id = message.from_user.id if message.from_user else 0
    ov = await referral.overview(tg_id)
    await message.answer(
        t("withdraw_ask_amount", lang, available=ov.available, min=referral.min_for_method(method)),
        reply_markup=keyboards.withdraw_cancel(lang),
    )


@router.message(WithdrawStates.entering_amount, F.text)
async def on_withdraw_amount(
    message: Message,
    state: FSMContext,
    referral: ReferralService,
    service: OrderService,
    settings: Settings,
    lang: str,
) -> None:
    raw = (message.text or "").strip().replace(",", ".").replace(" ", "")
    try:
        amount = Decimal(raw).quantize(Decimal("0.01"))
    except (InvalidOperation, ValueError):
        await message.answer(
            t("withdraw_not_number", lang), reply_markup=keyboards.withdraw_cancel(lang)
        )
        return
    data = await state.get_data()
    method = str(data.get("method", "sbp"))
    destination = str(data.get("destination", ""))
    tg_id = message.from_user.id if message.from_user else 0
    try:
        wd = await referral.create_withdrawal(
            tg_id=tg_id, method=method, destination=destination, amount=amount
        )
    except WithdrawalError as exc:
        await message.answer(
            t(exc.key, lang, **exc.params), reply_markup=keyboards.withdraw_cancel(lang)
        )
        return
    await state.clear()
    await message.answer(
        t("withdraw_created", lang, amount=wd.amount, id=wd.id),
        reply_markup=keyboards.main_menu(lang),
    )
    await _notify_admins_new_withdrawal(message, service, settings, wd)


@router.callback_query(F.data == "wd:cancel")
async def cb_withdraw_cancel(call: CallbackQuery, state: FSMContext, lang: str) -> None:
    await state.clear()
    await _render(call, t("withdraw_cancelled", lang), keyboards.main_menu(lang))
    await call.answer()


@router.callback_query(F.data == "wd:list")
async def cb_withdrawals(call: CallbackQuery, referral: ReferralService, lang: str) -> None:
    if not call.from_user:
        await call.answer()
        return
    items = await referral.list_withdrawals(call.from_user.id)
    if not items:
        text = t("withdrawals_empty", lang)
    else:
        lines = [t("withdrawals_header", lang)]
        for w in items:
            lines.append(
                t(
                    "withdrawal_line",
                    lang,
                    id=w.id,
                    amount=w.amount,
                    method=t(f"method_{w.method}", lang),
                    status=withdrawal_status_label(w.status, lang),
                )
            )
        text = "\n".join(lines)
    await _render(call, text, keyboards.referral_menu(lang, can_withdraw=False))
    await call.answer()


# ── Withdrawals (admin moderation) ───────────────────────────


async def _notify_admins_new_withdrawal(
    message: Message, service: OrderService, settings: Settings, wd
) -> None:
    user = message.from_user
    user_label = f"@{user.username}" if user and user.username else str(wd.user_tg_id)
    for admin_id in settings.admin_ids:
        alang = await service.get_user_language(admin_id)
        try:
            await message.bot.send_message(
                admin_id,
                t(
                    "admin_new_withdrawal",
                    alang,
                    id=wd.id,
                    user=user_label,
                    amount=wd.amount,
                    method=t(f"method_{wd.method}", alang),
                    destination=wd.destination,
                ),
                reply_markup=keyboards.admin_withdrawal_actions(wd.id, alang),
            )
        except Exception:
            logger.exception("failed to notify admin %s of withdrawal %s", admin_id, wd.id)


@router.callback_query(F.data.startswith("wda:approve:"))
async def cb_admin_approve(
    call: CallbackQuery, state: FSMContext, settings: Settings, lang: str
) -> None:
    if not (call.from_user and settings.is_admin(call.from_user.id)):
        await call.answer(t("admin_only", lang), show_alert=True)
        return
    wid = int((call.data or "").split(":")[-1])
    await state.set_state(AdminWithdrawStates.waiting_proof)
    await state.update_data(withdrawal_id=wid)
    if isinstance(call.message, Message):
        await call.message.answer(t("admin_ask_proof", lang, id=wid))
    await call.answer()


@router.callback_query(F.data.startswith("wda:reject:"))
async def cb_admin_reject(
    call: CallbackQuery, state: FSMContext, settings: Settings, lang: str
) -> None:
    if not (call.from_user and settings.is_admin(call.from_user.id)):
        await call.answer(t("admin_only", lang), show_alert=True)
        return
    wid = int((call.data or "").split(":")[-1])
    await state.set_state(AdminWithdrawStates.waiting_reject_reason)
    await state.update_data(withdrawal_id=wid)
    if isinstance(call.message, Message):
        await call.message.answer(t("admin_ask_reject_reason", lang, id=wid))
    await call.answer()


@router.message(AdminWithdrawStates.waiting_proof)
async def on_admin_proof(
    message: Message,
    state: FSMContext,
    referral: ReferralService,
    service: OrderService,
    lang: str,
) -> None:
    text = (message.text or "").strip()
    proof_type: str | None = None
    proof_value: str | None = None
    if text.startswith(("http://", "https://")):
        proof_type, proof_value = "link", text
    elif message.document and (
        message.document.mime_type == "application/pdf"
        or (message.document.file_name or "").lower().endswith(".pdf")
    ):
        proof_type, proof_value = "pdf", message.document.file_id
    if proof_type is None or proof_value is None:
        await message.answer(t("admin_proof_invalid", lang))
        return
    data = await state.get_data()
    wid = int(data.get("withdrawal_id", 0))
    admin_id = message.from_user.id if message.from_user else 0
    wd = await referral.approve_withdrawal(
        wid, admin_id=admin_id, proof_type=proof_type, proof_value=proof_value
    )
    await state.clear()
    if wd is None:
        await message.answer(t("admin_withdrawal_gone", lang))
        return
    await message.answer(t("admin_withdrawal_done", lang, id=wd.id))
    ulang = await service.get_user_language(wd.user_tg_id)
    caption = t("withdraw_approved_notify", ulang, id=wd.id, amount=wd.amount)
    try:
        if proof_type == "pdf":
            await message.bot.send_document(wd.user_tg_id, proof_value, caption=caption)
        else:
            await message.bot.send_message(wd.user_tg_id, f"{caption}\n{proof_value}")
    except Exception:
        logger.exception("failed to notify user %s of approval", wd.user_tg_id)


@router.message(AdminWithdrawStates.waiting_reject_reason, F.text)
async def on_admin_reject_reason(
    message: Message,
    state: FSMContext,
    referral: ReferralService,
    service: OrderService,
    lang: str,
) -> None:
    reason = (message.text or "").strip()
    data = await state.get_data()
    wid = int(data.get("withdrawal_id", 0))
    admin_id = message.from_user.id if message.from_user else 0
    wd = await referral.reject_withdrawal(wid, admin_id=admin_id, reason=reason)
    await state.clear()
    if wd is None:
        await message.answer(t("admin_withdrawal_gone", lang))
        return
    await message.answer(t("admin_withdrawal_done", lang, id=wd.id))
    ulang = await service.get_user_language(wd.user_tg_id)
    try:
        await message.bot.send_message(
            wd.user_tg_id, t("withdraw_rejected_notify", ulang, id=wd.id, reason=reason)
        )
    except Exception:
        logger.exception("failed to notify user %s of rejection", wd.user_tg_id)


# ── History & admin ──────────────────────────────────────────


@router.callback_query(F.data == "orders:list")
async def cb_orders(call: CallbackQuery, service: OrderService, lang: str) -> None:
    if not call.from_user:
        await call.answer()
        return
    orders = await service.list_orders(call.from_user.id)
    if not orders:
        text = t("orders_empty", lang)
    else:
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
        text = "\n".join(lines)
    await _render(call, text, keyboards.main_menu(lang))
    await call.answer()


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
