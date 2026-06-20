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
from app.bot.states import (
    AdminStates,
    AdminWithdrawStates,
    BuyStates,
    PartnerStates,
    WithdrawStates,
)
from app.config import Settings
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
) -> tuple[str, object]:
    """Build the main-menu text (with the 1⭐ rate) and the context-aware keyboard."""
    user = event.from_user
    bot_id = event.bot.id if event.bot else 0
    partner_markup = await partner.partner_markup(bot_id)
    rate = await service.star_rate(service.markup_percent + float(partner_markup))
    text = t("welcome", lang)
    if rate is not None:
        text += "\n\n" + t("star_rate", lang, rate=rate)
    is_owner = bool(user) and await partner.owner_of(bot_id) == user.id
    is_admin = bool(user) and settings.is_admin(user.id)
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
    text, kb = await _home_view(message, service, partner, settings, lang)
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
    lang: str,
) -> None:
    await state.clear()
    text, kb = await _home_view(call, service, partner, settings, lang)
    await _render(call, text, kb)
    await call.answer()


@router.callback_query(F.data == "buy:cancel")
async def cb_cancel(
    call: CallbackQuery,
    state: FSMContext,
    service: OrderService,
    partner: PartnerService,
    settings: Settings,
    lang: str,
) -> None:
    await state.clear()
    text, kb = await _home_view(call, service, partner, settings, lang)
    await _render(call, text, kb)
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
    await _render(call, t("ask_username", lang), keyboards.ask_username_kb(lang))
    await call.answer()


async def _set_target_username(
    event: Message | CallbackQuery,
    state: FSMContext,
    service: OrderService,
    username: str,
    lang: str,
) -> None:
    """Validate a recipient @username via WATA and advance to the count step."""
    username = username.strip().lstrip("@")
    if not _USERNAME_RE.match(username):
        await _render(event, t("username_invalid", lang), keyboards.ask_username_kb(lang))
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
    min_price_raw = data.get("min_price")
    if not target or min_price_raw is None:
        await _render(event, t("session_expired", lang), keyboards.main_menu(lang))
        await state.clear()
        return

    # Admin "topup" sells at the WATA floor (no markup). Otherwise, on a partner
    # bot, add the partner markup on top of the operator markup (compute_amount
    # clamps the total to WATA's +50% cap). The partner earns the difference over
    # what the operator alone would charge.
    min_price = Decimal(str(min_price_raw))
    bot_id = event.bot.id if event.bot else 0
    if data.get("no_markup"):
        operator = 0.0
        partner_markup = Decimal("0")
    else:
        operator = service.markup_percent
        partner_markup = await partner.partner_markup(bot_id)
    total_markup = operator + float(partner_markup)

    quote = compute_amount(min_price, count, total_markup)
    operator_amount = compute_amount(min_price, count, operator).amount
    partner_earning = quote.amount - operator_amount
    if partner_earning < 0:
        partner_earning = Decimal("0")
    partner_owner = await partner.owner_of(bot_id) if partner_earning > 0 else None

    await state.update_data(
        count=count,
        amount=str(quote.amount),
        partner_owner=partner_owner,
        partner_earning=str(partner_earning),
    )
    await state.set_state(BuyStates.confirm_payment)
    await _render(
        event,
        t("quote", lang, target=target, count=count, amount=quote.amount),
        keyboards.confirm_payment(lang),
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
    partner_owner = data.get("partner_owner")
    partner_earning = Decimal(str(data.get("partner_earning", "0")))
    try:
        created = await service.create_order(
            buyer_tg_id=user.id,
            buyer_username=user.username,
            target_username=str(target),
            count=int(count),
            amount=Decimal(str(amount)),
            partner_owner_tg_id=int(partner_owner) if partner_owner else None,
            partner_earning=partner_earning,
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


# ── Partner bots (managed bots) ──────────────────────────────


async def _register_partner_webhook(token: str, settings: Settings) -> None:
    """Point a freshly created managed bot's webhook at our multibot endpoint."""
    child = Bot(token, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
    try:
        url = f"{settings.public_base_url}/partner/{token}"
        await child.set_webhook(
            url=url, allowed_updates=["message", "callback_query"], drop_pending_updates=True
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
    text = t("partner_overview", lang, max=partner.max_partner_markup, count=len(bots))
    await _render(call, text, keyboards.partner_menu(lang, has_bots=bool(bots)))
    await call.answer()


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
            await _register_partner_webhook(bot.token, settings)
        else:
            await _delete_partner_webhook(bot.token)
    except Exception:
        logger.exception("failed to update webhook for partner bot %s", bot_id)
    await call.answer(t("partner_enabled" if bot.active else "partner_disabled", lang))
    # Re-render the view the toggle was triggered from.
    if call.bot and call.bot.id == bot_id:
        status = t("owner_status_on" if bot.active else "owner_status_off", lang)
        await _render(
            call,
            t(
                "owner_panel",
                lang,
                username=bot.username or bot.bot_id,
                markup=bot.markup_percent,
                max=partner.max_partner_markup,
                status=status,
            ),
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
    status = t("owner_status_on" if bot.active else "owner_status_off", lang)
    await _render(
        call,
        t(
            "owner_panel",
            lang,
            username=bot.username or bot.bot_id,
            markup=bot.markup_percent,
            max=partner.max_partner_markup,
            status=status,
        ),
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
        await _register_partner_webhook(token, settings)
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
    if not call.from_user:
        await call.answer()
        return
    bot_id = int((call.data or "").split(":")[-1])
    await state.set_state(PartnerStates.entering_markup)
    await state.update_data(markup_bot_id=bot_id)
    await _render(
        call,
        t("partner_ask_markup", lang, max=partner.max_partner_markup),
        keyboards.cancel_button(lang, data="menu:show"),
    )
    await call.answer()


@router.message(PartnerStates.entering_markup, F.text)
async def on_partner_markup(
    message: Message, state: FSMContext, partner: PartnerService, lang: str
) -> None:
    raw = (message.text or "").strip().replace(",", ".").replace("%", "")
    try:
        markup = Decimal(raw)
    except (InvalidOperation, ValueError):
        await message.answer(t("partner_markup_invalid", lang, max=partner.max_partner_markup))
        return
    if markup < 0 or markup > partner.max_partner_markup:
        await message.answer(t("partner_markup_invalid", lang, max=partner.max_partner_markup))
        return
    data = await state.get_data()
    bot_id = int(data.get("markup_bot_id", 0))
    bot = await partner.set_markup(bot_id, markup)
    await state.clear()
    if bot is None:
        await message.answer(
            t("partner_create_failed", lang), reply_markup=keyboards.main_menu(lang)
        )
        return
    await message.answer(
        t(
            "partner_markup_set",
            lang,
            username=bot.username or bot.bot_id,
            markup=bot.markup_percent,
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


# ── Admin panel ──────────────────────────────────────────────


def _is_admin_call(call: CallbackQuery, settings: Settings) -> bool:
    return bool(call.from_user and settings.is_admin(call.from_user.id))


@router.callback_query(F.data == "admin:show")
async def cb_admin(call: CallbackQuery, settings: Settings, lang: str) -> None:
    if not _is_admin_call(call, settings):
        await call.answer(t("admin_only", lang), show_alert=True)
        return
    await _render(call, t("admin_panel", lang), keyboards.admin_menu(lang))
    await call.answer()


@router.callback_query(F.data == "admin:stats")
async def cb_admin_stats(
    call: CallbackQuery, service: OrderService, settings: Settings, lang: str
) -> None:
    if not _is_admin_call(call, settings):
        await call.answer(t("admin_only", lang), show_alert=True)
        return
    d1 = await service.period_stats(1)
    d3 = await service.period_stats(3)
    d7 = await service.period_stats(7)
    d30 = await service.period_stats(30)
    text = t(
        "admin_stats",
        lang,
        d1_stars=d1["stars"], d1_orders=d1["orders"], d1_revenue=d1["revenue"],
        d3_stars=d3["stars"], d3_orders=d3["orders"], d3_revenue=d3["revenue"],
        d7_stars=d7["stars"], d7_orders=d7["orders"], d7_revenue=d7["revenue"],
        d30_stars=d30["stars"], d30_orders=d30["orders"], d30_revenue=d30["revenue"],
        d30_margin=d30["margin"],
    )
    await _render(call, text, keyboards.admin_menu(lang))
    await call.answer()


@router.callback_query(F.data == "admin:topup")
async def cb_admin_topup(
    call: CallbackQuery, state: FSMContext, settings: Settings, lang: str
) -> None:
    if not _is_admin_call(call, settings):
        await call.answer(t("admin_only", lang), show_alert=True)
        return
    await state.clear()
    await state.set_state(BuyStates.waiting_username)
    await state.update_data(no_markup=True)
    await _render(call, t("ask_username", lang), keyboards.ask_username_kb(lang))
    await call.answer()


@router.callback_query(F.data == "admin:finduser")
async def cb_admin_finduser(
    call: CallbackQuery, state: FSMContext, settings: Settings, lang: str
) -> None:
    if not _is_admin_call(call, settings):
        await call.answer(t("admin_only", lang), show_alert=True)
        return
    await state.set_state(AdminStates.entering_user_id)
    await _render(call, t("admin_ask_user_id", lang), keyboards.admin_menu(lang))
    await call.answer()


async def _show_admin_user(
    event: Message | CallbackQuery,
    service: OrderService,
    referral: ReferralService,
    uid: int,
    lang: str,
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
    await _render(event, text, keyboards.admin_user_actions(uid, user.banned, lang))


@router.message(AdminStates.entering_user_id, F.text)
async def on_admin_user_id(
    message: Message,
    state: FSMContext,
    service: OrderService,
    referral: ReferralService,
    settings: Settings,
    lang: str,
) -> None:
    if not (message.from_user and settings.is_admin(message.from_user.id)):
        await state.clear()
        return
    raw = (message.text or "").strip()
    if not raw.lstrip("-").isdigit():
        await message.answer(t("admin_bad_user_id", lang))
        return
    await state.clear()
    await _show_admin_user(message, service, referral, int(raw), lang)


@router.callback_query(F.data.startswith("admin:ban:"))
async def cb_admin_ban(
    call: CallbackQuery,
    service: OrderService,
    referral: ReferralService,
    settings: Settings,
    lang: str,
) -> None:
    if not _is_admin_call(call, settings):
        await call.answer(t("admin_only", lang), show_alert=True)
        return
    uid = int((call.data or "").split(":")[-1])
    await service.set_banned(uid, True)
    await call.answer(t("admin_user_banned", lang))
    await _show_admin_user(call, service, referral, uid, lang)


@router.callback_query(F.data.startswith("admin:unban:"))
async def cb_admin_unban(
    call: CallbackQuery,
    service: OrderService,
    referral: ReferralService,
    settings: Settings,
    lang: str,
) -> None:
    if not _is_admin_call(call, settings):
        await call.answer(t("admin_only", lang), show_alert=True)
        return
    uid = int((call.data or "").split(":")[-1])
    await service.set_banned(uid, False)
    await call.answer(t("admin_user_unbanned", lang))
    await _show_admin_user(call, service, referral, uid, lang)


@router.message(BuyStates.waiting_username)
@router.message(BuyStates.waiting_custom_count)
async def on_unexpected(message: Message, lang: str) -> None:
    await message.answer(t("send_text_or_cancel", lang))
