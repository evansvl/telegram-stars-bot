"""GreenGamePay error types. User-facing text is localized via i18n keys."""

from __future__ import annotations

DEFAULT_MESSAGE_KEY = "crypto_err_generic"


class GgpError(Exception):
    """Base class for all GreenGamePay client errors."""

    def message_key(self) -> str:
        return DEFAULT_MESSAGE_KEY


class GgpNetworkError(GgpError):
    """Network/timeout failure talking to the GreenGamePay API."""

    def message_key(self) -> str:
        return "crypto_err_network"


class GgpApiError(GgpError):
    """GreenGamePay returned a non-success HTTP status."""

    def __init__(
        self,
        status: int,
        message: str | None = None,
        payload: object | None = None,
    ) -> None:
        self.status = status
        self.message = message or ""
        self.payload = payload
        super().__init__(f"GGP API error status={status} message={message!r}")

    def message_key(self) -> str:
        if self.status == 401:
            return "crypto_err_auth"
        return DEFAULT_MESSAGE_KEY
