"""Shared buyer/referrer notification for terminal order outcomes.

Used by both the WATA webhook (``app/webhook/server.py``) and the TON poller
(``app/ton/poller.py``) so card and crypto orders notify identically: the buyer
is messaged through the bot the order was placed on (main or partner), the order
message is cleaned up on success, and the referrer/partner is credited once.
"""

from __future__ import annotations

import logging

from aiogram import Bot
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode

from app.bot import keyboards
from app.bot.i18n import t
from app.db.models import Order, OrderStatusEnum
from app.services import OrderService, PartnerService, ReferralService

logger = logging.getLogger(__name__)


async def notify_crypto_payment_verified(
    *,
    bot: Bot,
    service: OrderService,
    partner: PartnerService,
    order: Order,
) -> None:
    """Replace payment instructions once a TON transfer is matched on-chain."""
    if order.buyer_tg_id == 0:  # public website orders have no Telegram chat
        return
    lang = await service.get_user_language(order.buyer_tg_id)
    target_bot = bot
    spawned = False
    if order.bot_id and order.bot_id != bot.id:
        pbot = await partner.get_bot(order.bot_id)
        if pbot:
            target_bot = Bot(
                pbot.token,
                default=DefaultBotProperties(parse_mode=ParseMode.HTML),
            )
            spawned = True
    text = t(
        "crypto_payment_verified",
        lang,
        count=order.count,
        target=order.target_username,
    )
    markup = keyboards.crypto_verified_kb(lang, order.order_id)
    try:
        if order.chat_id and order.message_id:
            try:
                await target_bot.edit_message_text(
                    text,
                    chat_id=order.chat_id,
                    message_id=order.message_id,
                    reply_markup=markup,
                )
                return
            except Exception:
                logger.debug("could not replace crypto instructions order_id=%s", order.order_id)
        await target_bot.send_message(order.buyer_tg_id, text, reply_markup=markup)
    except Exception:
        logger.exception("failed to notify verified TON buyer tg_id=%s", order.buyer_tg_id)
    finally:
        if spawned:
            await target_bot.session.close()


async def _notify_admins_purchase(bot: Bot, service: OrderService, order: Order) -> None:
    """Tell every admin about a completed purchase (once, on delivery)."""
    buyer = (
        f"@{order.buyer.username}"
        if order.buyer and order.buyer.username
        else str(order.buyer_tg_id)
    )
    for admin_id in service.admin_ids:
        if admin_id == order.buyer_tg_id:
            continue
        lang = await service.get_user_language(admin_id)
        method = t(f"pay_method_{order.provider}", lang)
        try:
            await bot.send_message(
                admin_id,
                t(
                    "admin_purchase_notify",
                    lang,
                    count=order.count,
                    target=order.target_username,
                    amount=order.amount,
                    buyer=buyer,
                    method=method,
                ),
            )
        except Exception:
            logger.exception("failed to notify admin tg_id=%s", admin_id)


async def notify_order_outcome(
    *,
    bot: Bot,
    service: OrderService,
    referral: ReferralService,
    partner: PartnerService,
    order: Order,
    success: bool,
    notify_key: str,
) -> None:
    """Message the buyer about a terminal outcome and credit the referrer/partner.

    ``bot`` is the main bot; partner-bot orders are notified through the partner
    bot. Crediting is idempotent (one ``referral_earnings`` row per order).
    """
    lang = await service.get_user_language(order.buyer_tg_id)

    target_bot = bot
    spawned = False
    if order.bot_id and order.bot_id != bot.id:
        pbot = await partner.get_bot(order.bot_id)
        if pbot:
            target_bot = Bot(pbot.token, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
            spawned = True
    try:
        if success and order.chat_id and order.message_id:
            try:
                await target_bot.delete_message(order.chat_id, order.message_id)
            except Exception:
                logger.debug("could not delete order message order_id=%s", order.order_id)
        markup = keyboards.success_menu(lang) if success else keyboards.main_menu(lang)
        await target_bot.send_message(order.buyer_tg_id, t(notify_key, lang), reply_markup=markup)
    except Exception:
        logger.exception("failed to notify buyer tg_id=%s", order.buyer_tg_id)
    finally:
        if spawned:
            await target_bot.session.close()

    credited = await referral.credit_for_order(order)
    if credited:
        earner_id, reward = credited
        earn_lang = await service.get_user_language(earner_id)
        # A partner-bot sale pays the bot's owner their markup; a normal sale pays
        # the buyer's referrer. Tell each with the right wording.
        is_partner = bool(order.partner_owner_tg_id) and earner_id == order.partner_owner_tg_id
        if is_partner:
            notify_earn = t(
                "partner_earned_notify",
                earn_lang,
                amount=reward,
                count=order.count,
                target=order.target_username,
            )
        else:
            notify_earn = t("referral_earned_notify", earn_lang, amount=reward)
        try:
            await bot.send_message(earner_id, notify_earn)
        except Exception:
            logger.exception("failed to notify earner tg_id=%s", earner_id)

    # Tell admins about the sale, once, when stars are actually delivered.
    if order.status == OrderStatusEnum.SUCCESS.value:
        await _notify_admins_purchase(bot, service, order)
