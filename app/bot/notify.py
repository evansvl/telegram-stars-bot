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
from app.db.models import Order
from app.services import OrderService, PartnerService, ReferralService

logger = logging.getLogger(__name__)


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
        referrer_id, reward = credited
        ref_lang = await service.get_user_language(referrer_id)
        try:
            await bot.send_message(
                referrer_id, t("referral_earned_notify", ref_lang, amount=reward)
            )
        except Exception:
            logger.exception("failed to notify referrer tg_id=%s", referrer_id)
