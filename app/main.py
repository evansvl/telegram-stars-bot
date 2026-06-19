"""Application entrypoint.

Runs a single aiohttp server (behind your TLS reverse proxy) that serves BOTH:
  * Telegram updates  -> TELEGRAM_WEBHOOK_PATH (aiogram webhook)
  * WATA payment hooks -> WEBHOOK_PATH
plus a /health endpoint.
"""

from __future__ import annotations

import asyncio
import hashlib
import logging

import aiohttp
from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.fsm.storage.redis import RedisStorage
from aiogram.webhook.aiohttp_server import SimpleRequestHandler, setup_application
from aiohttp import web

from app.bot.handlers import router
from app.bot.middleware import LanguageMiddleware
from app.config import Settings, get_settings
from app.db.session import Database
from app.logging_config import setup_logging
from app.services import OrderService
from app.wata.client import WataClient
from app.wata.signature import SignatureVerifier
from app.webhook.server import build_webhook_app

logger = logging.getLogger(__name__)


def _telegram_secret(settings: Settings) -> str:
    """A non-empty Telegram secret_token, derived from the bot token if unset."""
    if settings.telegram_webhook_secret:
        return settings.telegram_webhook_secret
    return hashlib.sha256(settings.bot_token.encode()).hexdigest()


async def main() -> None:
    settings = get_settings()
    setup_logging(settings.log_level)

    if not settings.webhook_host:
        logger.critical("WEBHOOK_HOST is required (the bot runs in webhook mode). Set it in .env.")
        raise SystemExit(1)

    logger.info("starting telegram-stars-bot (webhook mode)")

    session = aiohttp.ClientSession()
    db = Database(settings.database_url)
    bot = Bot(
        token=settings.bot_token,
        default=DefaultBotProperties(parse_mode=ParseMode.HTML),
    )
    storage = RedisStorage.from_url(settings.redis_url)
    dp = Dispatcher(storage=storage)

    wata = WataClient(settings.wata_base_url, settings.wata_token, session)
    service = OrderService(settings, wata, db)
    verifier = SignatureVerifier(settings.wata_public_key_url, session)

    # Inject language into every handler; share settings/service via workflow_data.
    dp.message.middleware(LanguageMiddleware(service))
    dp.callback_query.middleware(LanguageMiddleware(service))
    dp.include_router(router)
    dp["settings"] = settings
    dp["service"] = service

    tg_secret = _telegram_secret(settings)

    # One aiohttp app serves WATA hooks, /health and Telegram updates.
    app = build_webhook_app(settings=settings, service=service, bot=bot, verifier=verifier)
    SimpleRequestHandler(dispatcher=dp, bot=bot, secret_token=tg_secret).register(
        app, path=settings.telegram_webhook_path
    )
    setup_application(app, dp, bot=bot)

    runner = web.AppRunner(app, access_log=None)
    await runner.setup()
    site = web.TCPSite(runner, host="0.0.0.0", port=settings.webhook_port)
    await site.start()
    logger.info("http server listening on 0.0.0.0:%d", settings.webhook_port)

    await bot.set_webhook(
        url=settings.telegram_webhook_url,
        secret_token=tg_secret,
        drop_pending_updates=True,
        allowed_updates=dp.resolve_used_update_types(),
    )
    logger.info("telegram webhook set url=%s", settings.telegram_webhook_url)
    logger.info("WATA webhook expected at url=%s", settings.webhook_url)

    stop_event = asyncio.Event()
    try:
        await stop_event.wait()
    finally:
        logger.info("shutting down")
        try:
            await bot.delete_webhook()
        except Exception:
            logger.exception("failed to delete telegram webhook")
        await runner.cleanup()
        await bot.session.close()
        await storage.close()
        await session.close()
        await db.dispose()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except (KeyboardInterrupt, SystemExit):
        logger.info("stopped")
