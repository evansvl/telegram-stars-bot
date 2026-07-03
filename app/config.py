"""Application configuration loaded from environment / .env via pydantic-settings."""

from __future__ import annotations

from decimal import Decimal

from pydantic import Field, field_validator, model_validator
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

    # ── Card rail: WATA Digital Goods API ────────────────────────
    # Enabled automatically when WATA_TOKEN is set (see the wata_enabled property).
    wata_token: str = ""
    wata_base_url: str = "https://dg-api.wata.pro/api"
    wata_public_key_url: str = "https://api.wata.pro/api/h2h/public-key"

    # Pricing
    markup_percent: float = 20.0  # card (WATA) markup
    auto_confirm: bool = True

    # ── Crypto rail: TON via GreenGamePay ────────────────────────
    # Enabled automatically when the GGP credentials + deposit address are all set
    # (see the crypto_enabled property). The buyer pays in TON to our GGP deposit
    # address, which tops up our GGP balance; we then deliver via GGP /api/buyStars.
    # Delivery is gated on the GGP balance (check_balance), not on scanning the
    # chain — the operator always receives the funds, so as soon as the balance
    # covers the purchase, buyStars runs. Crypto gets a cheaper star rate, so it
    # has its OWN, lower markup (added on top of the GGP wholesale TON price).
    crypto_markup_percent: float = 10.0
    # GreenGamePay credentials/host (secrets via env only, never committed).
    ggp_username: str = ""
    ggp_password: str = ""
    ggp_base_url: str = "https://xn--h1aahgceagbyl.xn--p1ai"  # пополнистим.рф (IDNA)
    # The TON deposit address buyers send to, and the comment that credits our
    # GGP balance (the operator login). Shown to the buyer as payment details.
    ggp_ton_address: str = ""
    ggp_deposit_memo: str = "evansvl"
    # How often (seconds) the background worker checks the GGP balance and delivers
    # pending crypto orders (oldest first).
    ton_poll_interval: int = 20
    crypto_payment_ttl_min: int = 30  # recommended pay-within window shown to buyer
    # Each pending crypto order gets a unique TON amount, spaced by this step.
    ton_amount_jitter_step: Decimal = Decimal("0.01")

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

    @field_validator("markup_percent", "crypto_markup_percent")
    @classmethod
    def _validate_markup(cls, value: float) -> float:
        if value < 0:
            raise ValueError("markup percent must be non-negative")
        return value

    @model_validator(mode="after")
    def _validate_rails(self) -> Settings:
        """At least one payment rail must be configured (card and/or crypto)."""
        if not (self.wata_enabled or self.crypto_enabled):
            raise ValueError(
                "no payment rail configured: set WATA_TOKEN (card) and/or the GGP "
                "credentials + GGP_TON_ADDRESS (crypto)"
            )
        return self

    @property
    def wata_enabled(self) -> bool:
        """Card rail is on when a WATA merchant token is configured."""
        return bool(self.wata_token)

    @property
    def crypto_enabled(self) -> bool:
        """Crypto rail is on when GGP credentials and a deposit address are set."""
        return bool(self.ggp_username and self.ggp_password and self.ggp_ton_address)

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
