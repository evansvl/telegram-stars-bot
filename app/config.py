"""Application configuration loaded from environment / .env via pydantic-settings."""

from __future__ import annotations

from decimal import Decimal

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Strongly-typed application settings.

    Values are read from environment variables (and a local ``.env`` during
    development). All secrets stay out of the repository.
    """

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    # Telegram
    bot_token: str = Field(..., min_length=1)

    # WATA Digital Goods API
    wata_token: str = Field(..., min_length=1)
    wata_base_url: str = "https://dg-api.wata.pro/api"
    wata_public_key_url: str = "https://api.wata.pro/api/h2h/public-key"

    # Pricing
    markup_percent: float = 20.0
    auto_confirm: bool = True

    # Test mode: skip WATA entirely. Orders get a "mark as paid" button instead of
    # a real payment link, so the referral/partner flows can be tested end-to-end.
    test_mode: bool = False

    # WATA limits the number of concurrent unpaid orders per buyer account. We
    # block a new order past this many recent unpaid ones with a clear message.
    max_active_orders: int = 3

    # Access control. Read as a plain string (a simple type pydantic-settings never
    # JSON-decodes) and expose the parsed list via the admin_ids property below.
    admin_ids_raw: str = Field(default="", validation_alias="admin_ids")

    # Infrastructure
    database_url: str = "postgresql+asyncpg://bot:bot@postgres:5432/wata_bot"
    redis_url: str = "redis://redis:6379/0"

    # Webhook server (HTTP behind a user-managed reverse proxy with TLS).
    # The same server serves both the Telegram and the WATA webhooks.
    webhook_host: str = ""
    webhook_port: int = 8080

    # WATA payment notifications.
    webhook_path: str = "/wata/webhook"
    webhook_secret: str = ""

    # Telegram updates (aiogram webhook). secret_token is sent by Telegram in the
    # X-Telegram-Bot-Api-Secret-Token header and verified by aiogram.
    telegram_webhook_path: str = "/tg/webhook"
    telegram_webhook_secret: str = ""

    # Reference @username used to quote the displayed "1 ⭐ rate" (cached ~1h).
    rate_reference_username: str = "hahahahgaha"

    # Referral program: reward as a percentage of the referred user's gross payment.
    referral_percent: float = 5.0
    # Partner bots: the operator's cut, as a percent of the partner's own markup.
    # Partners set their markup on top of MARKUP_PERCENT and earn it minus this cut;
    # buyers on a partner bot pay more, so this cut is pure extra take for you.
    partner_commission_percent: float = 10.0
    # Minimum payout amount (in RUB) per withdrawal method.
    withdraw_min_sbp: Decimal = Decimal("500")
    withdraw_min_crypto: Decimal = Decimal("1000")

    # Legal: links shown in the bot. Leave empty to hide the button/command.
    terms_url: str = "https://telegra.ph/POLZOVATELSKOE-SOGLASHENIE-PUBLICHNAYA-OFERTA-06-19"
    privacy_url: str = (
        "https://telegra.ph/POLITIKA-KONFIDENCIALNOSTI-I-OBRABOTKI-PERSONALNYH-DANNYH-06-19"
    )
    support_url: str = "https://t.me/nnstorecontactbot"

    log_level: str = "INFO"

    @field_validator("markup_percent")
    @classmethod
    def _validate_markup(cls, value: float) -> float:
        if value < 0:
            raise ValueError("MARKUP_PERCENT must be non-negative")
        return value

    @property
    def admin_ids(self) -> list[int]:
        """Admin Telegram IDs parsed from the comma-separated ADMIN_IDS env var."""
        return [int(part) for part in self.admin_ids_raw.replace(" ", "").split(",") if part]

    @property
    def public_base_url(self) -> str:
        return f"https://{self.webhook_host.rstrip('/')}"

    @property
    def webhook_url(self) -> str:
        """Full public URL that must be registered in the WATA dashboard."""
        return f"{self.public_base_url}{self.webhook_path}"

    @property
    def telegram_webhook_url(self) -> str:
        """Full public URL Telegram will POST updates to."""
        return f"{self.public_base_url}{self.telegram_webhook_path}"

    def is_admin(self, tg_id: int) -> bool:
        return tg_id in self.admin_ids


_settings: Settings | None = None


def get_settings() -> Settings:
    """Return a cached Settings instance (lazy singleton)."""
    global _settings
    if _settings is None:
        _settings = Settings()  # type: ignore[call-arg]
    return _settings
