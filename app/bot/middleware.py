"""Middleware that resolves the user's language and injects it as ``lang``."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

from aiogram import BaseMiddleware
from aiogram.types import CallbackQuery, Message, TelegramObject, User

from app.bot.i18n import DEFAULT_LANG
from app.services import OrderService


class LanguageMiddleware(BaseMiddleware):
    """Adds ``lang`` to handler data based on the stored/Telegram language."""

    def __init__(self, service: OrderService) -> None:
        self._service = service

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
            data["lang"] = await self._service.resolve_language(user.id, user.language_code)
        else:
            data["lang"] = DEFAULT_LANG
        return await handler(event, data)
