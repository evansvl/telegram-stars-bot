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
from typing import Any

from aiogram import Bot, F, Router
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.exceptions import TelegramBadRequest
from aiogram.filters import Command, CommandObject, CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.methods import GetManagedBotToken
from aiogram.types import CallbackQuery, InlineKeyboardMarkup, Message, ReplyKeyboardRemove

from app.bot import keyboards
from app.bot.i18n import normalize_lang, status_label, t, withdrawal_status_label
from app.bot.notify import notify_order_outcome
from app.bot.states import (
    AdminStates,
    AdminWithdrawStates,
    BuyStates,
    PartnerStates,
    WithdrawStates,
)
from app.bot.webhook import (
    partner_webhook_secret,
    partner_webhook_url,
    telegram_webhook_master_secret,
)
from app.config import Settings
from app.db.models import Withdrawal
from app.ggp.errors import GgpError
from app.pricing import MAX_STARS, MIN_STARS, compute_amount, is_valid_count
from app.services import OrderService, PartnerService, ReferralService, WithdrawalError
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


async def _home_view(
    event: Message | CallbackQuery,
    service: OrderService,
    partner: PartnerService,
    settings: Settings,
    lang: str,
    main_bot: Bot | None = None,
) -> tuple[str, InlineKeyboardMarkup]:
    """Build the main-menu text (with the 1⭐ rate) and the context-aware keyboard."""
    user = event.from_user
    bot_id = event.bot.id if event.bot else 0
    text = t("welcome", lang)
    # No card rate in crypto-only mode (no WATA).
    card_rate = await service.star_rate(service.markup_percent) if service.wata_enabled else None
    crypto_rate = await service.crypto_star_rate()
    if card_rate is not None and crypto_rate is not None:
        text += "\n\n" + t("star_rate_both", lang, card=card_rate, crypto=crypto_rate)
    elif crypto_rate is not None:
        text += "\n\n" + t("star_rate_crypto", lang, rate=crypto_rate)
    elif card_rate is not None:
        text += "\n\n" + t("star_rate", lang, rate=card_rate)
    is_owner = user is not None and await partner.owner_of(bot_id) == user.id
    is_admin = bool(
        user is not None
        and main_bot is not None
        and event.bot is not None
        and event.bot.id == main_bot.id
        and settings.is_admin(user.id)
    )
    return text, keyboards.main_menu(lang, owner_settings=is_owner, admin=is_admin)


@router.message(CommandStart())
async def cmd_start(
    message: Message,
    state: FSMContext,
    command: CommandObject,
    service: OrderService,
    referral: ReferralService,
    partner: PartnerService,
    settings: Settings,
    main_bot: Bot,
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
    text, kb = await _home_view(message, service, partner, settings, lang, main_bot)
    await message.answer(text, reply_markup=kb)


@router.callback_query(F.data == "help:show")
async def cb_help(call: CallbackQuery, lang: str) -> None:
    await _render(call, t("help", lang), keyboards.help_menu(lang))
    await call.answer()


@router.callback_query(F.data == "menu:show")
async def cb_menu(
    call: CallbackQuery,
    state: FSMContext,
    service: OrderService,
    partner: PartnerService,
    settings: Settings,
    main_bot: Bot,
    lang: str,
) -> None:
    await state.clear()
    text, kb = await _home_view(call, service, partner, settings, lang, main_bot)
    await _render(call, text, kb)
    await call.answer()


@router.callback_query(F.data == "buy:cancel")
async def cb_cancel(
    call: CallbackQuery,
    state: FSMContext,
    service: OrderService,
    partner: PartnerService,
    settings: Settings,
    main_bot: Bot,
    lang: str,
) -> None:
    await state.clear()
    text, kb = await _home_view(call, service, partner, settings, lang, main_bot)
    await _render(call, text, kb)
    await call.answer()


# ── Language ─────────────────────────────────────────────────


@router.callback_query(F.data == "lang:choose")
async def cb_language_choose(call: CallbackQuery, lang: str) -> None:
    await _render(call, t("choose_language", lang), keyboards.language_picker(lang))
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
    await _render(call, t("ask_username", lang), keyboards.ask_username_kb(lang))
    await call.answer()


async def _set_target_username(
    event: Message | CallbackQuery,
    state: FSMContext,
    service: OrderService,
    username: str,
    lang: str,
) -> None:
    """Validate a recipient @username and advance to the count step.

    Card mode validates the username via WATA (and caches its ``minPrice``); with
    no card rail there's no WATA, so we accept a well-formed @username and let GGP
    validate it at delivery time.
    """
    username = username.strip().lstrip("@")
    if not _USERNAME_RE.match(username):
        await _render(event, t("username_invalid", lang), keyboards.ask_username_kb(lang))
        return
    if not service.wata_enabled:
        await state.update_data(target_username=username, min_price=None)
        await state.set_state(BuyStates.waiting_count)
        await _render(
            event, t("user_found", lang, username=username), keyboards.count_presets(lang)
        )
        return
    try:
        price = await service.get_star_price(username)
    except WataError as exc:
        await _render(
            event,
            f"⚠️ {t(exc.message_key(), lang)}\n\n{t('enter_other_username', lang)}",
            keyboards.ask_username_kb(lang),
        )
        return

    await state.update_data(target_username=username, min_price=str(price.min_price))
    await state.set_state(BuyStates.waiting_count)
    await _render(event, t("user_found", lang, username=username), keyboards.count_presets(lang))


@router.callback_query(BuyStates.waiting_username, F.data == "buy:self")
async def cb_buy_self(
    call: CallbackQuery, state: FSMContext, service: OrderService, lang: str
) -> None:
    user = call.from_user
    if not user or not user.username:
        await call.answer(t("no_username", lang), show_alert=True)
        return
    await _set_target_username(call, state, service, user.username, lang)
    await call.answer()


@router.message(BuyStates.waiting_username, F.text)
async def on_username(
    message: Message, state: FSMContext, service: OrderService, lang: str
) -> None:
    await _set_target_username(message, state, service, message.text or "", lang)


async def _show_quote(
    event: Message | CallbackQuery,
    state: FSMContext,
    service: OrderService,
    partner: PartnerService,
    count: int,
    lang: str,
) -> None:
    data = await state.get_data()
    target = data.get("target_username")
    if not target:
        await _render(event, t("session_expired", lang), keyboards.main_menu(lang))
        await state.clear()
        return

    # Buyers pay the operator's price (admin "topup" pays the floor). On a partner
    # bot the buyer also pays the partner's own markup on top, and the partner earns
    # it minus the operator's cut. Rails: card (WATA) and crypto (TON via GGP), each
    # with its own markup. In crypto-only mode there's no card rail.
    bot_id = event.bot.id if event.bot else 0
    no_markup = bool(data.get("no_markup"))
    partner_owner = None if no_markup else await partner.owner_of(bot_id)
    partner_markup = (
        await partner.partner_markup(bot_id) if partner_owner is not None else Decimal("0")
    )

    # Explicitly clear both rails before rebuilding the quote. FSM updates merge
    # with existing data, so leaving old values here would let a forged callback
    # reuse a payment method that the new quote no longer offers.
    fsm: dict[str, Any] = {
        "count": count,
        "partner_owner": partner_owner,
        "card_amount": None,
        "card_partner_earning": None,
        "crypto_ton_due": None,
        "crypto_rub_equiv": None,
        "crypto_rub_cost": None,
        "crypto_partner_earning": None,
    }

    # ── Card (WATA) ──
    card_amount: Decimal | None = None
    min_price_raw = data.get("min_price")
    if service.wata_enabled and min_price_raw is not None:
        min_price = Decimal(str(min_price_raw))
        operator = 0.0 if no_markup else service.markup_percent
        if partner_owner is not None:
            card_quote = compute_amount(min_price, count, operator + float(partner_markup))
            base = compute_amount(min_price, count, operator)
            card_partner_earning = partner.partner_share(card_quote.amount - base.amount)
        else:
            card_quote = compute_amount(min_price, count, operator)
            card_partner_earning = Decimal("0")
        card_amount = card_quote.amount
        fsm["card_amount"] = str(card_amount)
        fsm["card_partner_earning"] = str(card_partner_earning)

    # ── Crypto (TON via GGP), best-effort ──
    crypto_ton: Decimal | None = None
    if service.crypto_enabled:
        crypto_operator = 0.0 if no_markup else service.crypto_markup_percent
        try:
            cq = await service.crypto_quote(count, crypto_operator + float(partner_markup))
        except GgpError:
            logger.warning("GGP quote failed")
        else:
            crypto_partner_earning = (
                partner.partner_share(cq.rub_cost * partner_markup / Decimal("100"))
                if partner_owner is not None
                else Decimal("0")
            )
            crypto_ton = cq.ton_due
            fsm.update(
                crypto_ton_due=str(cq.ton_due),
                crypto_rub_equiv=str(cq.rub_equiv),
                crypto_rub_cost=str(cq.rub_cost),
                crypto_partner_earning=str(crypto_partner_earning),
            )

    if card_amount is None and crypto_ton is None:
        # Nothing to offer (e.g. no card rail and the GGP quote failed).
        key = "crypto_unavailable" if not service.wata_enabled else "session_expired"
        await _render(event, t(key, lang), keyboards.main_menu(lang))
        await state.clear()
        return

    await state.update_data(**fsm)
    await state.set_state(BuyStates.confirm_payment)
    lines: list[str] = []
    if card_amount is not None:
        lines.append(t("quote_line_card", lang, card=card_amount))
    if crypto_ton is not None:
        lines.append(t("quote_line_crypto", lang, ton=crypto_ton))
    text = (
        t("quote_header", lang, target=target, count=count)
        + "\n\n"
        + "\n".join(lines)
        + "\n\n"
        + t("quote_footer", lang)
    )
    await _render(
        event,
        text,
        keyboards.choose_method(lang, card=card_amount is not None, crypto=crypto_ton is not None),
    )


@router.callback_query(BuyStates.waiting_count, F.data.startswith("count:"))
async def cb_count(
    call: CallbackQuery,
    state: FSMContext,
    service: OrderService,
    partner: PartnerService,
    lang: str,
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

    await _show_quote(call, state, service, partner, int(value), lang)
    await call.answer()


@router.message(BuyStates.waiting_custom_count, F.text)
async def on_custom_count(
    message: Message,
    state: FSMContext,
    service: OrderService,
    partner: PartnerService,
    lang: str,
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
    await _show_quote(message, state, service, partner, count, lang)


@router.callback_query(BuyStates.confirm_payment, F.data == "pay:card")
async def cb_pay(
    call: CallbackQuery,
    state: FSMContext,
    service: OrderService,
    settings: Settings,
    lang: str,
) -> None:
    data = await state.get_data()
    target = data.get("target_username")
    count = data.get("count")
    amount = data.get("card_amount")
    if not (target and count and amount) or not call.from_user:
        await call.answer(t("session_expired_alert", lang), show_alert=True)
        await state.clear()
        return

    user = call.from_user
    # WATA caps concurrent unpaid orders per account; block the extra one cleanly.
    if await service.active_order_count(user.id) >= settings.max_active_orders:
        await call.answer()
        await _render(
            call,
            t("too_many_orders", lang, limit=settings.max_active_orders),
            keyboards.main_menu(lang),
        )
        await state.clear()
        return

    await call.answer(t("creating_order", lang))
    partner_owner = data.get("partner_owner")
    partner_earning = Decimal(str(data.get("card_partner_earning", "0")))
    # Remember the order message so the payment webhook can delete it on success.
    msg = call.message if isinstance(call.message, Message) else None
    # Send the buyer back to *this* bot after paying (works for partner bots too).
    redirect_url = None
    if call.bot:
        me = await call.bot.me()
        if me.username:
            redirect_url = f"https://t.me/{me.username}"
    try:
        created = await service.create_order(
            buyer_tg_id=user.id,
            buyer_username=user.username,
            target_username=str(target),
            count=int(count),
            amount=Decimal(str(amount)),
            partner_owner_tg_id=int(partner_owner) if partner_owner else None,
            partner_earning=partner_earning,
            bot_id=call.bot.id if call.bot else None,
            chat_id=msg.chat.id if msg else None,
            message_id=msg.message_id if msg else None,
            redirect_url=redirect_url,
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


@router.callback_query(BuyStates.confirm_payment, F.data == "pay:crypto")
async def cb_pay_crypto(
    call: CallbackQuery,
    state: FSMContext,
    service: OrderService,
    settings: Settings,
    lang: str,
) -> None:
    data = await state.get_data()
    target = data.get("target_username")
    count = data.get("count")
    ton_due = data.get("crypto_ton_due")
    rub_equiv = data.get("crypto_rub_equiv")
    rub_cost = data.get("crypto_rub_cost")
    if not (target and count and ton_due and rub_equiv) or not call.from_user:
        await call.answer(t("session_expired_alert", lang), show_alert=True)
        await state.clear()
        return
    if not service.crypto_enabled:
        await call.answer(t("crypto_unavailable", lang), show_alert=True)
        return

    user = call.from_user
    # One unpaid crypto order at a time: the buyer must pay it, cancel it, or wait
    # for it to expire before starting another — this keeps the delivery queue clean
    # and avoids two same-priced orders being satisfied by a single deposit.
    existing = await service.pending_crypto_order(user.id)
    if existing is not None:
        # Re-show the unpaid order's details so they can pay or cancel it.
        await call.answer(
            t("crypto_already_pending", lang, ttl=service.crypto_payment_ttl_min),
            show_alert=True,
        )
        await _render(
            call,
            t(
                "crypto_instructions",
                lang,
                count=existing.count,
                target=existing.target_username,
                ton=existing.ton_amount,
                address=settings.ggp_ton_address,
                memo=settings.ggp_deposit_memo,
                ttl=service.crypto_payment_ttl_min,
            ),
            keyboards.crypto_instructions_kb(lang, existing.order_id),
        )
        await state.clear()
        return

    await call.answer(t("creating_order", lang))
    partner_owner = data.get("partner_owner")
    partner_earning = Decimal(str(data.get("crypto_partner_earning", "0")))
    msg = call.message if isinstance(call.message, Message) else None
    created = await service.create_crypto_order(
        buyer_tg_id=user.id,
        buyer_username=user.username,
        target_username=str(target),
        count=int(count),
        ton_due=Decimal(str(ton_due)),
        rub_equiv=Decimal(str(rub_equiv)),
        rub_cost=Decimal(str(rub_cost or "0")),
        partner_owner_tg_id=int(partner_owner) if partner_owner else None,
        partner_earning=partner_earning,
        bot_id=call.bot.id if call.bot else None,
        chat_id=msg.chat.id if msg else None,
        message_id=msg.message_id if msg else None,
    )
    await state.clear()
    await _render(
        call,
        t(
            "crypto_instructions",
            lang,
            count=created.count,
            target=created.target_username,
            ton=created.ton_amount,
            address=created.address,
            memo=created.memo,
            ttl=created.ttl_min,
        ),
        keyboards.crypto_instructions_kb(lang, created.order_id),
    )


@router.callback_query(F.data.startswith("crypto:paid:"))
async def cb_crypto_paid(
    call: CallbackQuery,
    main_bot: Bot,
    service: OrderService,
    referral: ReferralService,
    partner: PartnerService,
    lang: str,
) -> None:
    """Buyer says they paid: check the GGP balance and deliver if funded."""
    if not call.from_user:
        await call.answer()
        return
    order_id = (call.data or "").split(":")[-1]
    if not service.crypto_enabled:
        await call.answer(t("crypto_unavailable", lang), show_alert=True)
        return
    await call.answer(t("crypto_checking", lang))
    try:
        delivered, outcome = await service.try_deliver_crypto(
            order_id, buyer_tg_id=call.from_user.id
        )
    except GgpError:
        await call.answer(t("crypto_err_network", lang), show_alert=True)
        return
    if outcome == "delivered" and delivered is not None:
        await notify_order_outcome(
            bot=main_bot,
            service=service,
            referral=referral,
            partner=partner,
            order=delivered,
            success=True,
            notify_key="notify_Success",
        )
    elif outcome == "failed" and delivered is not None:
        await notify_order_outcome(
            bot=main_bot,
            service=service,
            referral=referral,
            partner=partner,
            order=delivered,
            success=False,
            notify_key="crypto_failed_contact",
        )
    elif outcome != "gone":
        await call.answer(t("crypto_not_funded", lang), show_alert=True)


@router.callback_query(F.data.startswith("crypto:cancel:"))
async def cb_crypto_cancel(
    call: CallbackQuery,
    service: OrderService,
    partner: PartnerService,
    settings: Settings,
    main_bot: Bot,
    lang: str,
) -> None:
    """Buyer cancels their pending crypto order, freeing the slot for a new one."""
    if not call.from_user:
        await call.answer()
        return
    order_id = (call.data or "").split(":")[-1]
    cancelled = await service.cancel_crypto_order(order_id, call.from_user.id)
    await call.answer(t("crypto_cancelled" if cancelled else "crypto_cancel_gone", lang))
    text, markup = await _home_view(call, service, partner, settings, lang, main_bot)
    await _render(call, text, markup)


@router.callback_query(F.data.startswith("testpay:"))
async def cb_testpay(
    call: CallbackQuery,
    service: OrderService,
    referral: ReferralService,
    settings: Settings,
    lang: str,
) -> None:
    """Simulate a successful payment (test mode) and run the payout flow."""
    if not settings.test_mode or not call.from_user:
        logger.warning("rejected testpay callback while test mode is disabled")
        await call.answer()
        return
    order_id = (call.data or "").split(":", 1)[1]
    order = await service.simulate_payment(order_id, call.from_user.id)
    await call.answer()
    if order is None:
        return
    await _render(call, t("test_paid_done", lang), keyboards.success_menu(lang))
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


# ── Referral program ─────────────────────────────────────────


def _fmt_percent(value: float) -> str:
    return str(int(value)) if value == int(value) else str(value)


@router.callback_query(F.data.startswith("ref:show"))
async def cb_referral(call: CallbackQuery, referral: ReferralService, lang: str) -> None:
    if not call.from_user:
        await call.answer()
        return
    # Opened from the profile -> back to profile; from the main menu -> back home.
    back_data = "profile:show" if (call.data or "").endswith(":profile") else "menu:show"
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
    await _render(
        call, text, keyboards.referral_menu(lang, can_withdraw=can_withdraw, back_data=back_data)
    )
    await call.answer()


# ── Partner bots (managed bots) ──────────────────────────────


async def _register_partner_webhook(token: str, bot_id: int, settings: Settings) -> None:
    """Point a freshly created managed bot's webhook at our multibot endpoint."""
    child = Bot(token, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
    try:
        master_secret = telegram_webhook_master_secret(settings)
        await child.set_webhook(
            url=partner_webhook_url(settings, bot_id),
            secret_token=partner_webhook_secret(master_secret, bot_id),
            allowed_updates=["message", "callback_query"],
            drop_pending_updates=True,
        )
    finally:
        await child.session.close()


async def _delete_partner_webhook(token: str) -> None:
    child = Bot(token, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
    try:
        await child.delete_webhook(drop_pending_updates=False)
    finally:
        await child.session.close()


@router.callback_query(F.data == "partner:show")
async def cb_partner_show(call: CallbackQuery, partner: PartnerService, lang: str) -> None:
    if not call.from_user:
        await call.answer()
        return
    bots = await partner.list_for_owner(call.from_user.id)
    text = t(
        "partner_overview",
        lang,
        max=_fmt_percent(float(partner.max_partner_markup)),
        cut=_fmt_percent(float(partner.operator_cut_percent)),
        count=len(bots),
    )
    await _render(call, text, keyboards.partner_menu(lang, has_bots=bool(bots)))
    await call.answer()


def _owner_panel_text(partner: PartnerService, bot, lang: str) -> str:
    status = t("owner_status_on" if bot.active else "owner_status_off", lang)
    return t(
        "owner_panel",
        lang,
        username=bot.username or bot.bot_id,
        markup=_fmt_percent(float(bot.markup_percent)),
        max=_fmt_percent(float(partner.max_partner_markup)),
        cut=_fmt_percent(float(partner.operator_cut_percent)),
        status=status,
    )


@router.callback_query(F.data == "partner:create")
async def cb_partner_create(call: CallbackQuery, partner: PartnerService, lang: str) -> None:
    if call.from_user and await partner.has_bot(call.from_user.id):
        await call.answer(t("partner_only_one", lang), show_alert=True)
        return
    if isinstance(call.message, Message):
        await call.message.answer(
            t("partner_create_prompt", lang), reply_markup=keyboards.partner_create_kb(lang)
        )
    await call.answer()


@router.callback_query(F.data.startswith("partner:toggle:"))
async def cb_partner_toggle(
    call: CallbackQuery, partner: PartnerService, settings: Settings, lang: str
) -> None:
    user = call.from_user
    bot_id = int((call.data or "").split(":")[-1])
    if not user or await partner.owner_of(bot_id) != user.id:
        await call.answer(t("admin_only", lang), show_alert=True)
        return
    bot = await partner.toggle_bot(bot_id)
    if bot is None:
        await call.answer()
        return
    try:
        if bot.active:
            await _register_partner_webhook(bot.token, bot.bot_id, settings)
        else:
            await _delete_partner_webhook(bot.token)
    except Exception:
        logger.exception("failed to update webhook for partner bot %s", bot_id)
    await call.answer(t("partner_enabled" if bot.active else "partner_disabled", lang))
    # Re-render the view the toggle was triggered from.
    if call.bot and call.bot.id == bot_id:
        await _render(
            call,
            _owner_panel_text(partner, bot, lang),
            keyboards.owner_panel(lang, bot.bot_id, bot.active),
        )
    else:
        bots = await partner.list_for_owner(user.id)
        await _render(call, t("partner_bots_header", lang), keyboards.partner_bots_kb(lang, bots))


@router.callback_query(F.data == "owner:settings")
async def cb_owner_settings(call: CallbackQuery, partner: PartnerService, lang: str) -> None:
    user = call.from_user
    bot_id = call.bot.id if call.bot else 0
    bot = await partner.get_bot(bot_id)
    if not user or bot is None or bot.owner_tg_id != user.id:
        await call.answer(t("admin_only", lang), show_alert=True)
        return
    await _render(
        call,
        _owner_panel_text(partner, bot, lang),
        keyboards.owner_panel(lang, bot.bot_id, bot.active),
    )
    await call.answer()


@router.message(F.managed_bot_created)
async def on_managed_bot_created(
    message: Message, partner: PartnerService, settings: Settings, lang: str
) -> None:
    created = message.managed_bot_created
    if created is None or not message.from_user:
        return
    if await partner.has_bot(message.from_user.id):
        await message.answer(t("partner_only_one", lang), reply_markup=ReplyKeyboardRemove())
        return
    bot_user = created.bot_user
    try:
        token = await message.bot(GetManagedBotToken(user_id=bot_user.id))
    except Exception:
        logger.exception("failed to get managed bot token for %s", bot_user.id)
        await message.answer(t("partner_create_failed", lang), reply_markup=ReplyKeyboardRemove())
        return
    await partner.create_bot(
        owner_tg_id=message.from_user.id,
        bot_id=bot_user.id,
        username=bot_user.username,
        token=token,
    )
    try:
        await _register_partner_webhook(token, bot_user.id, settings)
    except Exception:
        logger.exception("failed to register partner webhook for %s", bot_user.id)
    await message.answer(
        t("partner_bot_created", lang, username=bot_user.username or bot_user.id),
        reply_markup=ReplyKeyboardRemove(),
    )


@router.callback_query(F.data == "partner:list")
async def cb_partner_list(call: CallbackQuery, partner: PartnerService, lang: str) -> None:
    if not call.from_user:
        await call.answer()
        return
    bots = await partner.list_for_owner(call.from_user.id)
    if not bots:
        await _render(
            call, t("partner_bots_empty", lang), keyboards.partner_menu(lang, has_bots=False)
        )
    else:
        await _render(call, t("partner_bots_header", lang), keyboards.partner_bots_kb(lang, bots))
    await call.answer()


@router.callback_query(F.data.startswith("partner:setmarkup:"))
async def cb_partner_setmarkup(
    call: CallbackQuery, state: FSMContext, partner: PartnerService, lang: str
) -> None:
    user = call.from_user
    bot_id = int((call.data or "").split(":")[-1])
    if not user or await partner.owner_of(bot_id) != user.id:
        await call.answer(t("admin_only", lang), show_alert=True)
        return
    await state.set_state(PartnerStates.entering_markup)
    await state.update_data(markup_bot_id=bot_id)
    await _render(
        call,
        t("partner_ask_markup", lang, max=_fmt_percent(float(partner.max_partner_markup))),
        keyboards.cancel_button(lang),
    )
    await call.answer()


@router.message(PartnerStates.entering_markup, F.text)
async def on_partner_markup(
    message: Message, state: FSMContext, partner: PartnerService, lang: str
) -> None:
    max_markup = partner.max_partner_markup
    max_label = _fmt_percent(float(max_markup))
    raw = (message.text or "").strip().replace(",", ".").replace("%", "").replace(" ", "")
    try:
        value = Decimal(raw)
    except (InvalidOperation, ValueError):
        await message.answer(
            t("partner_markup_invalid", lang, max=max_label),
            reply_markup=keyboards.cancel_button(lang),
        )
        return
    if value < 0 or value > max_markup:
        await message.answer(
            t("partner_markup_invalid", lang, max=max_label),
            reply_markup=keyboards.cancel_button(lang),
        )
        return
    data = await state.get_data()
    bot_id = int(data.get("markup_bot_id", 0))
    if not message.from_user or await partner.owner_of(bot_id) != message.from_user.id:
        await state.clear()
        return
    bot = await partner.set_markup(bot_id, value)
    await state.clear()
    if bot is None:
        await message.answer(t("partner_bots_empty", lang), reply_markup=keyboards.main_menu(lang))
        return
    await message.answer(
        t(
            "partner_markup_set",
            lang,
            username=bot.username or bot.bot_id,
            markup=_fmt_percent(float(bot.markup_percent)),
        ),
        reply_markup=keyboards.main_menu(lang),
    )


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
    main_bot: Bot,
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
    await _notify_admins_new_withdrawal(message, main_bot, service, settings, wd)


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
    await _render(call, text, keyboards.withdrawals_menu(lang))
    await call.answer()


# ── Withdrawals (admin moderation) ───────────────────────────


async def _notify_admins_new_withdrawal(
    message: Message,
    main_bot: Bot,
    service: OrderService,
    settings: Settings,
    wd: Withdrawal,
) -> None:
    user = message.from_user
    user_label = f"@{user.username}" if user and user.username else str(wd.user_tg_id)
    origin_bot_id = message.bot.id if message.bot else main_bot.id
    for admin_id in settings.admin_ids:
        alang = await service.get_user_language(admin_id)
        try:
            await main_bot.send_message(
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
                reply_markup=keyboards.admin_withdrawal_actions(
                    wd.id, alang, origin_bot_id=origin_bot_id
                ),
            )
        except Exception:
            logger.exception("failed to notify admin %s of withdrawal %s", admin_id, wd.id)


def _parse_withdrawal_callback(data: str | None) -> tuple[int, int | None] | None:
    """Parse current ``wda:action:id:origin_bot`` and legacy three-part callbacks."""
    parts = (data or "").split(":")
    if len(parts) not in {3, 4} or not parts[2].isdecimal():
        return None
    if len(parts) == 4 and not parts[3].isdecimal():
        return None
    return int(parts[2]), int(parts[3]) if len(parts) == 4 else None


async def _withdrawal_notification_bot(
    origin_bot_id: int | None, main_bot: Bot, partner: PartnerService
) -> tuple[Bot, bool]:
    """Resolve the bot the user actually contacted; return ``(bot, must_close)``."""
    if not origin_bot_id or origin_bot_id == main_bot.id:
        return main_bot, False
    partner_bot = await partner.get_bot(origin_bot_id)
    if partner_bot is None or not partner_bot.active:
        return main_bot, False
    return (
        Bot(
            partner_bot.token,
            default=DefaultBotProperties(parse_mode=ParseMode.HTML),
        ),
        True,
    )


@router.callback_query(F.data.startswith("wda:approve:"))
async def cb_admin_approve(
    call: CallbackQuery,
    state: FSMContext,
    settings: Settings,
    main_bot: Bot,
    lang: str,
) -> None:
    if not _is_admin_call(call, settings, main_bot):
        await call.answer(t("admin_only", lang), show_alert=True)
        return
    parsed = _parse_withdrawal_callback(call.data)
    if parsed is None:
        logger.warning("rejected malformed withdrawal callback data=%r", call.data)
        await call.answer()
        return
    wid, origin_bot_id = parsed
    await state.set_state(AdminWithdrawStates.waiting_proof)
    await state.update_data(withdrawal_id=wid, withdrawal_bot_id=origin_bot_id)
    if isinstance(call.message, Message):
        await call.message.answer(t("admin_ask_proof", lang, id=wid))
    await call.answer()


@router.callback_query(F.data.startswith("wda:reject:"))
async def cb_admin_reject(
    call: CallbackQuery,
    state: FSMContext,
    settings: Settings,
    main_bot: Bot,
    lang: str,
) -> None:
    if not _is_admin_call(call, settings, main_bot):
        await call.answer(t("admin_only", lang), show_alert=True)
        return
    parsed = _parse_withdrawal_callback(call.data)
    if parsed is None:
        logger.warning("rejected malformed withdrawal callback data=%r", call.data)
        await call.answer()
        return
    wid, origin_bot_id = parsed
    await state.set_state(AdminWithdrawStates.waiting_reject_reason)
    await state.update_data(withdrawal_id=wid, withdrawal_bot_id=origin_bot_id)
    if isinstance(call.message, Message):
        await call.message.answer(t("admin_ask_reject_reason", lang, id=wid))
    await call.answer()


@router.message(AdminWithdrawStates.waiting_proof)
async def on_admin_proof(
    message: Message,
    state: FSMContext,
    referral: ReferralService,
    service: OrderService,
    partner: PartnerService,
    settings: Settings,
    main_bot: Bot,
    lang: str,
) -> None:
    if not _is_admin_message(message, settings, main_bot):
        await state.clear()
        return
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
    origin_bot_id = data.get("withdrawal_bot_id")
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
    notify_bot, close_notify_bot = await _withdrawal_notification_bot(
        int(origin_bot_id) if origin_bot_id else None, main_bot, partner
    )
    try:
        if proof_type == "pdf":
            await notify_bot.send_document(wd.user_tg_id, proof_value, caption=caption)
        else:
            await notify_bot.send_message(wd.user_tg_id, f"{caption}\n{proof_value}")
    except Exception:
        logger.exception("failed to notify user %s of approval", wd.user_tg_id)
    finally:
        if close_notify_bot:
            await notify_bot.session.close()


@router.message(AdminWithdrawStates.waiting_reject_reason, F.text)
async def on_admin_reject_reason(
    message: Message,
    state: FSMContext,
    referral: ReferralService,
    service: OrderService,
    partner: PartnerService,
    settings: Settings,
    main_bot: Bot,
    lang: str,
) -> None:
    if not _is_admin_message(message, settings, main_bot):
        await state.clear()
        return
    reason = (message.text or "").strip()
    data = await state.get_data()
    wid = int(data.get("withdrawal_id", 0))
    origin_bot_id = data.get("withdrawal_bot_id")
    admin_id = message.from_user.id if message.from_user else 0
    wd = await referral.reject_withdrawal(wid, admin_id=admin_id, reason=reason)
    await state.clear()
    if wd is None:
        await message.answer(t("admin_withdrawal_gone", lang))
        return
    await message.answer(t("admin_withdrawal_done", lang, id=wd.id))
    ulang = await service.get_user_language(wd.user_tg_id)
    notify_bot, close_notify_bot = await _withdrawal_notification_bot(
        int(origin_bot_id) if origin_bot_id else None, main_bot, partner
    )
    try:
        await notify_bot.send_message(
            wd.user_tg_id, t("withdraw_rejected_notify", ulang, id=wd.id, reason=reason)
        )
    except Exception:
        logger.exception("failed to notify user %s of rejection", wd.user_tg_id)
    finally:
        if close_notify_bot:
            await notify_bot.session.close()


# ── History & admin ──────────────────────────────────────────


@router.callback_query(F.data == "profile:show")
async def cb_profile(
    call: CallbackQuery, service: OrderService, referral: ReferralService, lang: str
) -> None:
    if not call.from_user:
        await call.answer()
        return
    profile = await service.buyer_profile(call.from_user.id)
    ov = await referral.overview(call.from_user.id)
    registered = profile.registered.strftime("%Y-%m-%d") if profile.registered else "—"
    text = t(
        "profile",
        lang,
        id=call.from_user.id,
        registered=registered,
        stars=profile.stars,
        orders=profile.orders,
        spent=profile.spent,
        referrals=ov.referrals,
        earned=ov.earned,
        available=ov.available,
    )
    await _render(call, text, keyboards.profile_menu(lang))
    await call.answer()


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
    await _render(call, text, keyboards.orders_menu(lang))
    await call.answer()


@router.message(Command("stats"))
async def cmd_stats(
    message: Message,
    service: OrderService,
    settings: Settings,
    main_bot: Bot,
    lang: str,
) -> None:
    if not _is_admin_message(message, settings, main_bot):
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


# ── Admin panel ──────────────────────────────────────────────


def _is_admin_call(call: CallbackQuery, settings: Settings, main_bot: Bot) -> bool:
    return bool(
        call.bot
        and call.bot.id == main_bot.id
        and call.from_user
        and settings.is_admin(call.from_user.id)
    )


def _is_admin_message(message: Message, settings: Settings, main_bot: Bot) -> bool:
    return bool(
        message.bot
        and message.bot.id == main_bot.id
        and message.from_user
        and settings.is_admin(message.from_user.id)
    )


@router.callback_query(F.data == "admin:show")
async def cb_admin(
    call: CallbackQuery, settings: Settings, main_bot: Bot, lang: str
) -> None:
    if not _is_admin_call(call, settings, main_bot):
        await call.answer(t("admin_only", lang), show_alert=True)
        return
    await _render(call, t("admin_panel", lang), keyboards.admin_menu(lang))
    await call.answer()


@router.callback_query(F.data == "admin:stats")
async def cb_admin_stats(
    call: CallbackQuery,
    service: OrderService,
    referral: ReferralService,
    settings: Settings,
    main_bot: Bot,
    lang: str,
) -> None:
    if not _is_admin_call(call, settings, main_bot):
        await call.answer(t("admin_only", lang), show_alert=True)
        return
    d1 = await service.period_stats(1)
    d3 = await service.period_stats(3)
    d7 = await service.period_stats(7)
    d30 = await service.period_stats(30)
    users = await service.user_count()
    ref_earned, ref_outstanding = await referral.bot_totals()
    text = t(
        "admin_stats",
        lang,
        d1_stars=d1["stars"], d1_orders=d1["orders"], d1_revenue=d1["revenue"],
        d3_stars=d3["stars"], d3_orders=d3["orders"], d3_revenue=d3["revenue"],
        d7_stars=d7["stars"], d7_orders=d7["orders"], d7_revenue=d7["revenue"],
        d30_stars=d30["stars"], d30_orders=d30["orders"], d30_revenue=d30["revenue"],
        d30_margin=d30["margin"],
        users=users,
        ref_earned=ref_earned,
        ref_outstanding=ref_outstanding,
    )
    await _render(call, text, keyboards.admin_back(lang))
    await call.answer()


@router.callback_query(F.data == "admin:topup")
async def cb_admin_topup(
    call: CallbackQuery,
    state: FSMContext,
    settings: Settings,
    main_bot: Bot,
    lang: str,
) -> None:
    if not _is_admin_call(call, settings, main_bot):
        await call.answer(t("admin_only", lang), show_alert=True)
        return
    await state.clear()
    await state.set_state(BuyStates.waiting_username)
    await state.update_data(no_markup=True)
    await _render(call, t("ask_username", lang), keyboards.ask_username_kb(lang))
    await call.answer()


ADMIN_USERS_PAGE_SIZE = 6


async def _show_users_page(event: Message | CallbackQuery, service: OrderService, lang: str,
                          page: int) -> None:
    """Render one page of the user browser (fetches one extra to detect a next page)."""
    page = max(0, page)
    rows = await service.list_users_page(page * ADMIN_USERS_PAGE_SIZE, ADMIN_USERS_PAGE_SIZE + 1)
    has_next = len(rows) > ADMIN_USERS_PAGE_SIZE
    users = rows[:ADMIN_USERS_PAGE_SIZE]
    header = "admin_users_header" if users else "admin_users_empty"
    await _render(
        event,
        t(header, lang),
        keyboards.admin_users_kb(users, lang, page=page, has_next=has_next),
    )


@router.callback_query(F.data == "admin:finduser")
async def cb_admin_finduser(
    call: CallbackQuery,
    state: FSMContext,
    service: OrderService,
    settings: Settings,
    main_bot: Bot,
    lang: str,
) -> None:
    if not _is_admin_call(call, settings, main_bot):
        await call.answer(t("admin_only", lang), show_alert=True)
        return
    await state.clear()
    await _show_users_page(call, service, lang, 0)
    await call.answer()


@router.callback_query(F.data.startswith("admin:users:"))
async def cb_admin_users_page(
    call: CallbackQuery,
    state: FSMContext,
    service: OrderService,
    settings: Settings,
    main_bot: Bot,
    lang: str,
) -> None:
    if not _is_admin_call(call, settings, main_bot):
        await call.answer(t("admin_only", lang), show_alert=True)
        return
    await state.clear()
    page = int((call.data or "").split(":")[-1])
    await _show_users_page(call, service, lang, page)
    await call.answer()


@router.callback_query(F.data == "admin:search")
async def cb_admin_search(
    call: CallbackQuery,
    state: FSMContext,
    settings: Settings,
    main_bot: Bot,
    lang: str,
) -> None:
    if not _is_admin_call(call, settings, main_bot):
        await call.answer(t("admin_only", lang), show_alert=True)
        return
    await state.set_state(AdminStates.entering_user_id)
    await _render(call, t("admin_ask_user_id", lang), keyboards.admin_back(lang))
    await call.answer()


@router.callback_query(F.data.startswith("admin:user:"))
async def cb_admin_user(
    call: CallbackQuery,
    service: OrderService,
    referral: ReferralService,
    settings: Settings,
    main_bot: Bot,
    lang: str,
) -> None:
    if not _is_admin_call(call, settings, main_bot):
        await call.answer(t("admin_only", lang), show_alert=True)
        return
    parts = (call.data or "").split(":")
    uid = int(parts[2])
    page = int(parts[3]) if len(parts) > 3 else 0
    await _show_admin_user(call, service, referral, uid, lang, page=page)
    await call.answer()


async def _show_admin_user(
    event: Message | CallbackQuery,
    service: OrderService,
    referral: ReferralService,
    uid: int,
    lang: str,
    *,
    page: int = 0,
) -> None:
    user = await service.get_user(uid)
    if user is None:
        await _render(event, t("admin_user_not_found", lang), keyboards.admin_menu(lang))
        return
    profile = await service.buyer_profile(uid)
    ov = await referral.overview(uid)
    status = t("admin_status_banned" if user.banned else "admin_status_active", lang)
    registered = profile.registered.strftime("%Y-%m-%d") if profile.registered else "—"
    text = t(
        "admin_user_profile",
        lang,
        id=uid,
        username=f"@{user.username}" if user.username else "—",
        registered=registered,
        status=status,
        stars=profile.stars,
        orders=profile.orders,
        spent=profile.spent,
        referrals=ov.referrals,
        earned=ov.earned,
        available=ov.available,
    )
    await _render(event, text, keyboards.admin_user_actions(uid, user.banned, lang, page=page))


@router.message(AdminStates.entering_user_id, F.text)
async def on_admin_user_query(
    message: Message,
    state: FSMContext,
    service: OrderService,
    referral: ReferralService,
    settings: Settings,
    main_bot: Bot,
    lang: str,
) -> None:
    if not _is_admin_message(message, settings, main_bot):
        await state.clear()
        return
    raw = (message.text or "").strip()
    uid: int | None = None
    if raw.lstrip("-").isdigit():
        uid = int(raw)
    else:
        username = raw.lstrip("@").strip()
        if username:
            user = await service.find_user_by_username(username)
            if user:
                uid = user.tg_id
    if uid is None:
        # Stay in the search state so the admin can retry.
        await message.answer(t("admin_bad_user_id", lang))
        return
    await state.clear()
    await _show_admin_user(message, service, referral, uid, lang)


@router.callback_query(F.data.startswith("admin:ban:"))
async def cb_admin_ban(
    call: CallbackQuery,
    service: OrderService,
    referral: ReferralService,
    settings: Settings,
    main_bot: Bot,
    lang: str,
) -> None:
    if not _is_admin_call(call, settings, main_bot):
        await call.answer(t("admin_only", lang), show_alert=True)
        return
    parts = (call.data or "").split(":")
    uid = int(parts[2])
    page = int(parts[3]) if len(parts) > 3 else 0
    await service.set_banned(uid, True)
    await call.answer(t("admin_user_banned", lang))
    await _show_admin_user(call, service, referral, uid, lang, page=page)


@router.callback_query(F.data.startswith("admin:unban:"))
async def cb_admin_unban(
    call: CallbackQuery,
    service: OrderService,
    referral: ReferralService,
    settings: Settings,
    main_bot: Bot,
    lang: str,
) -> None:
    if not _is_admin_call(call, settings, main_bot):
        await call.answer(t("admin_only", lang), show_alert=True)
        return
    parts = (call.data or "").split(":")
    uid = int(parts[2])
    page = int(parts[3]) if len(parts) > 3 else 0
    await service.set_banned(uid, False)
    await call.answer(t("admin_user_unbanned", lang))
    await _show_admin_user(call, service, referral, uid, lang, page=page)


@router.message(BuyStates.waiting_username)
@router.message(BuyStates.waiting_custom_count)
async def on_unexpected(message: Message, lang: str) -> None:
    await message.answer(t("send_text_or_cancel", lang))
