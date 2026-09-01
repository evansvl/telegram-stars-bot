"""Authenticated webhook handling for partner-managed Telegram bots."""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import logging
from typing import TYPE_CHECKING, Any

from aiogram import Bot, Dispatcher
from aiogram.webhook.aiohttp_server import BaseRequestHandler
from aiohttp import web

from app.config import Settings

if TYPE_CHECKING:
    from app.services import PartnerService

logger = logging.getLogger(__name__)

PARTNER_WEBHOOK_PATH = "/partner/{bot_id}"
_PARTNER_SECRET_DOMAIN = b"telegram-partner-webhook:v1:"


def telegram_webhook_master_secret(settings: Settings) -> str:
    """Derive the Telegram webhook secret deterministically from ``BOT_TOKEN``."""
    return hashlib.sha256(settings.bot_token.encode()).hexdigest()


def partner_webhook_secret(master_secret: str, bot_id: int) -> str:
    """Derive one Bot API-compatible secret token for a partner bot."""
    if not master_secret:
        raise ValueError("webhook master secret must not be empty")
    if bot_id <= 0:
        raise ValueError("bot_id must be positive")
    message = _PARTNER_SECRET_DOMAIN + str(bot_id).encode("ascii")
    # A lowercase SHA-256 hex digest is 64 chars from Telegram's allowed
    # A-Z/a-z/0-9/_/- alphabet (the Bot API permits 1..256 chars).
    return hmac.new(master_secret.encode(), message, hashlib.sha256).hexdigest()


def partner_webhook_url(settings: Settings, bot_id: int) -> str:
    """Return a partner webhook URL containing only the public bot ID."""
    if bot_id <= 0:
        raise ValueError("bot_id must be positive")
    return f"{settings.public_base_url}/partner/{bot_id}"


class PartnerRequestHandler(BaseRequestHandler):
    """Resolve active partner bots from the DB and authenticate every update."""

    def __init__(
        self,
        dispatcher: Dispatcher,
        partner: PartnerService,
        master_secret: str,
        handle_in_background: bool = True,
        bot_settings: dict[str, Any] | None = None,
        **data: Any,
    ) -> None:
        if not master_secret:
            raise ValueError("webhook master secret must not be empty")
        super().__init__(
            dispatcher=dispatcher,
            handle_in_background=handle_in_background,
            **data,
        )
        self.partner = partner
        self.master_secret = master_secret
        self.bot_settings = bot_settings or {}
        self.bots: dict[int, Bot] = {}
        self._bots_lock = asyncio.Lock()

    def register(self, app: web.Application, /, path: str, **kwargs: Any) -> None:
        if "{bot_id}" not in path:
            raise ValueError("path must contain '{bot_id}'")
        super().register(app, path=path, **kwargs)

    async def resolve_bot(self, request: web.Request) -> Bot:
        raw_bot_id = request.match_info.get("bot_id", "")
        try:
            bot_id = int(raw_bot_id)
        except ValueError as exc:
            raise web.HTTPNotFound() from exc
        if bot_id <= 0:
            raise web.HTTPNotFound()

        partner_bot = await self.partner.get_bot(bot_id)
        if partner_bot is None or not partner_bot.active:
            raise web.HTTPNotFound()

        async with self._bots_lock:
            cached = self.bots.get(bot_id)
            if cached is not None and cached.token == partner_bot.token:
                return cached
            if cached is not None:
                await cached.session.close()

            bot = Bot(token=partner_bot.token, **self.bot_settings)
            # Do not let a corrupt/mismatched DB row route updates under another
            # bot identity, even if its per-bot header happens to be valid.
            if bot.id != bot_id:
                await bot.session.close()
                logger.error("partner bot token/id mismatch bot_id=%s", bot_id)
                raise web.HTTPNotFound()
            self.bots[bot_id] = bot
            return bot

    def verify_secret(self, telegram_secret_token: str, bot: Bot) -> bool:
        expected = partner_webhook_secret(self.master_secret, bot.id)
        return hmac.compare_digest(telegram_secret_token, expected)

    async def close(self) -> None:
        for bot in self.bots.values():
            await bot.session.close()
        self.bots.clear()
