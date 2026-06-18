"""Application entrypoint: starts the bot (long-polling) and the webhook server."""

from __future__ import annotations

import asyncio
import logging

import aiohttp
from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.fsm.storage.redis import RedisStorage
from aiohttp import web

from app.bot.handlers import router
from app.config import get_settings
from app.db.session import Database
from app.logging_config import setup_logging
from app.services import OrderService
from app.wata.client import WataClient
from app.wata.signature import SignatureVerifier
from app.webhook.server import build_webhook_app

logger = logging.getLogger(__name__)


async def _run_webhook(app: web.Application, host: str, port: int) -> web.AppRunner:
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, host=host, port=port)
    await site.start()
    logger.info("webhook server listening host=%s port=%d", host, port)
    return runner


async def main() -> None:
    settings = get_settings()
    setup_logging(settings.log_level)
    logger.info("starting telegram-stars-bot")

    session = aiohttp.ClientSession()
    db = Database(settings.database_url)
    bot = Bot(
        token=settings.bot_token,
        default=DefaultBotProperties(parse_mode=ParseMode.HTML),
    )
    storage = RedisStorage.from_url(settings.redis_url)
    dp = Dispatcher(storage=storage)
    dp.include_router(router)

    wata = WataClient(settings.wata_base_url, settings.wata_token, session)
    service = OrderService(settings, wata, db)
    verifier = SignatureVerifier(settings.wata_public_key_url, session)

    # Dependencies injected into handlers by parameter name.
    dp["settings"] = settings
    dp["service"] = service

    webhook_app = build_webhook_app(
        settings=settings, service=service, bot=bot, verifier=verifier
    )
    runner = await _run_webhook(webhook_app, "0.0.0.0", settings.webhook_port)

    try:
        await bot.delete_webhook(drop_pending_updates=True)
        await dp.start_polling(bot, settings=settings, service=service)
    finally:
        logger.info("shutting down")
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
