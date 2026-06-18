"""aiohttp webhook server receiving WATA payment notifications."""

from app.webhook.server import build_webhook_app

__all__ = ["build_webhook_app"]
