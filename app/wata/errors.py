"""WATA error types and mapping of API error codes to user-facing messages."""

from __future__ import annotations

# Map documented WATA error codes to friendly Russian messages for end users.
WATA_ERROR_MESSAGES: dict[str, str] = {
    "STR_1001": "Переданы неверные данные заказа. Попробуйте ещё раз.",
    "STR_1002": "Пользователь Telegram с таким @username не найден.",
    "STR_1003": "Количество звёзд должно быть от 50 до 50 000.",
    "STR_1004": "Не удалось создать заказ на стороне WATA. Попробуйте позже.",
    "ORD_1001": "Заказ не найден.",
    "ORD_1004": "Истёк срок оплаты заказа. Создайте новый заказ.",
    "PL_1003": "Ссылка на оплату недоступна или уже оплачена.",
}

DEFAULT_USER_MESSAGE = "Произошла ошибка при обращении к платёжному сервису. Попробуйте позже."


class WataError(Exception):
    """Base class for all WATA client errors."""

    def user_message(self) -> str:
        return DEFAULT_USER_MESSAGE


class WataNetworkError(WataError):
    """Network/timeout failure talking to the WATA API."""

    def __init__(self, message: str) -> None:
        super().__init__(message)

    def user_message(self) -> str:
        return "Платёжный сервис временно недоступен. Попробуйте через минуту."


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

    def user_message(self) -> str:
        if self.code and self.code in WATA_ERROR_MESSAGES:
            return WATA_ERROR_MESSAGES[self.code]
        if self.status == 401:
            return "Ошибка авторизации платёжного сервиса (проверьте WATA_TOKEN)."
        if self.status == 429:
            return "Слишком много запросов к платёжному сервису. Подождите немного."
        return DEFAULT_USER_MESSAGE
