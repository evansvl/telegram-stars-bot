"""Middleware: resolve the user's language (as ``lang``) and block banned users."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

from aiogram import BaseMiddleware
from aiogram.types import CallbackQuery, Message, TelegramObject, User

from app.bot.i18n import DEFAULT_LANG
from app.config import Settings
from app.services import OrderService


class LanguageMiddleware(BaseMiddleware):
    """Adds ``lang`` to handler data and drops updates from banned users."""

    def __init__(self, service: OrderService, settings: Settings) -> None:
        self._service = service
        self._settings = settings

    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        user: User | None = None
        if isinstance(event, (Message, CallbackQuery)):
            user = event.from_user
        if user is not None:
            # Banned users are silently ignored (admins are never blocked).
            if not self._settings.is_admin(user.id) and await self._service.is_banned(user.id):
                return None
            data["lang"] = await self._service.resolve_language(user.id, user.language_code)
        else:
            data["lang"] = DEFAULT_LANG
        return await handler(event, data)
