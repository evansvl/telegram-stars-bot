"""WATA error types. User-facing text is localized via i18n message keys."""

from __future__ import annotations

# Map documented WATA error codes to i18n message keys (see app.bot.i18n).
WATA_ERROR_KEYS: dict[str, str] = {
    "STR_1001": "err_invalid_data",
    "STR_1002": "err_user_not_found",
    "STR_1003": "err_count_range",
    "STR_1004": "err_create_failed",
    "ORD_1001": "err_order_not_found",
    "ORD_1004": "err_payment_expired",
    "PL_1003": "err_link_unavailable",
}

DEFAULT_MESSAGE_KEY = "err_generic"


class WataError(Exception):
    """Base class for all WATA client errors."""

    def message_key(self) -> str:
        """i18n key for a user-facing message (resolved by the bot layer)."""
        return DEFAULT_MESSAGE_KEY


class WataNetworkError(WataError):
    """Network/timeout failure talking to the WATA API."""

    def message_key(self) -> str:
        return "err_network"


class WataApiError(WataError):
    """WATA returned a non-success HTTP status and/or an error code."""

    def __init__(
        self,
        status: int,
        code: str | None = None,
        message: str | None = None,
        payload: object | None = None,
    ) -> None:
        self.status = status
        self.code = code
        self.message = message or ""
        self.payload = payload
        super().__init__(f"WATA API error status={status} code={code} message={message!r}")

    def message_key(self) -> str:
        if self.code and self.code in WATA_ERROR_KEYS:
            return WATA_ERROR_KEYS[self.code]
        if self.status == 401:
            return "err_auth"
        if self.status == 429:
            return "err_rate_limit"
        return DEFAULT_MESSAGE_KEY
