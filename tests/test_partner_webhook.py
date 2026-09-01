"""Authentication tests for partner-bot Telegram webhooks."""

from __future__ import annotations

import hashlib
import hmac
import re
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer
from app.bot.webhook import (
    PARTNER_WEBHOOK_PATH,
    PartnerRequestHandler,
    partner_webhook_secret,
    partner_webhook_url,
    telegram_webhook_master_secret,
)

MASTER_SECRET = "server-only-master-secret"
BOT_ID = 123456
BOT_TOKEN = f"{BOT_ID}:partner-token"


class FakePartnerService:
    def __init__(self, bots: dict[int, object]) -> None:
        self.bots = bots

    async def get_bot(self, bot_id: int) -> object | None:
        return self.bots.get(bot_id)


def test_partner_secret_is_per_bot_hmac_and_bot_api_compatible() -> None:
    secret = partner_webhook_secret(MASTER_SECRET, BOT_ID)
    expected = hmac.new(
        MASTER_SECRET.encode(),
        b"telegram-partner-webhook:v1:" + str(BOT_ID).encode("ascii"),
        hashlib.sha256,
    ).hexdigest()

    assert secret == expected
    assert secret != partner_webhook_secret(MASTER_SECRET, BOT_ID + 1)
    assert re.fullmatch(r"[A-Za-z0-9_-]{1,256}", secret)


def test_partner_webhook_url_contains_id_not_token() -> None:
    settings = SimpleNamespace(public_base_url="https://bot.example")

    url = partner_webhook_url(settings, BOT_ID)  # type: ignore[arg-type]

    assert url == f"https://bot.example/partner/{BOT_ID}"
    assert BOT_TOKEN not in url


def test_telegram_webhook_secret_is_always_derived_from_main_bot_token() -> None:
    settings = SimpleNamespace(
        bot_token="999999:main-token",
        # A legacy manual value must have no effect.
        telegram_webhook_secret="must-be-ignored",
    )

    assert telegram_webhook_master_secret(settings) == hashlib.sha256(
        settings.bot_token.encode()
    ).hexdigest()


@pytest.mark.asyncio
async def test_partner_registration_sends_derived_secret(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.bot import handlers

    child = SimpleNamespace(
        set_webhook=AsyncMock(return_value=True),
        session=SimpleNamespace(close=AsyncMock()),
    )
    bot_factory = Mock(return_value=child)
    monkeypatch.setattr(handlers, "Bot", bot_factory)
    settings = SimpleNamespace(
        public_base_url="https://bot.example",
        bot_token="999999:main-token",
    )
    derived_master = hashlib.sha256(settings.bot_token.encode()).hexdigest()

    await handlers._register_partner_webhook(BOT_TOKEN, BOT_ID, settings)  # type: ignore[arg-type]

    child.set_webhook.assert_awaited_once_with(
        url=f"https://bot.example/partner/{BOT_ID}",
        secret_token=partner_webhook_secret(derived_master, BOT_ID),
        allowed_updates=["message", "callback_query"],
        drop_pending_updates=True,
    )
    child.session.close.assert_awaited_once()


@pytest.mark.asyncio
async def test_partner_webhook_requires_matching_secret_header() -> None:
    partner = FakePartnerService(
        {BOT_ID: SimpleNamespace(bot_id=BOT_ID, token=BOT_TOKEN, active=True)}
    )
    dispatcher = SimpleNamespace(feed_webhook_update=AsyncMock(return_value=None))
    handler = PartnerRequestHandler(
        dispatcher=dispatcher,  # type: ignore[arg-type]
        partner=partner,  # type: ignore[arg-type]
        master_secret=MASTER_SECRET,
        handle_in_background=False,
    )
    app = web.Application()
    handler.register(app, PARTNER_WEBHOOK_PATH)

    async with TestClient(TestServer(app)) as client:
        missing = await client.post(f"/partner/{BOT_ID}", json={"update_id": 1})
        wrong = await client.post(
            f"/partner/{BOT_ID}",
            json={"update_id": 2},
            headers={"X-Telegram-Bot-Api-Secret-Token": "wrong"},
        )
        valid = await client.post(
            f"/partner/{BOT_ID}",
            json={"update_id": 3},
            headers={
                "X-Telegram-Bot-Api-Secret-Token": partner_webhook_secret(
                    MASTER_SECRET, BOT_ID
                )
            },
        )

        assert missing.status == 401
        assert wrong.status == 401
        assert valid.status == 200

    dispatcher.feed_webhook_update.assert_awaited_once()


@pytest.mark.asyncio
@pytest.mark.parametrize("record", [None, SimpleNamespace(active=False, token=BOT_TOKEN)])
async def test_partner_webhook_rejects_unknown_or_inactive_bot(record: object | None) -> None:
    partner = FakePartnerService({} if record is None else {BOT_ID: record})
    dispatcher = SimpleNamespace(feed_webhook_update=AsyncMock(return_value=None))
    handler = PartnerRequestHandler(
        dispatcher=dispatcher,  # type: ignore[arg-type]
        partner=partner,  # type: ignore[arg-type]
        master_secret=MASTER_SECRET,
        handle_in_background=False,
    )
    app = web.Application()
    handler.register(app, PARTNER_WEBHOOK_PATH)

    async with TestClient(TestServer(app)) as client:
        response = await client.post(
            f"/partner/{BOT_ID}",
            json={"update_id": 1},
            headers={
                "X-Telegram-Bot-Api-Secret-Token": partner_webhook_secret(
                    MASTER_SECRET, BOT_ID
                )
            },
        )

        assert response.status == 404

    dispatcher.feed_webhook_update.assert_not_awaited()
