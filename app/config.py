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
    # Enabled automatically when the complete GGP configuration is present. Incoming
    # deposits are verified on-chain before GGP is asked to deliver the stars.
    crypto_markup_percent: float = 10.0
    # GreenGamePay credentials/host (secrets via env only, never committed).
    ggp_username: str = ""
    ggp_password: str = ""
    ggp_base_url: str = "https://xn--h1aahgceagbyl.xn--p1ai"  # пополнистим.рф (IDNA)
    # The TON deposit address buyers send to, and the comment that credits our
    # GGP balance (the operator login). Shown to the buyer as payment details.
    ggp_ton_address: str = ""
    # Empty means "use GGP_USERNAME", preventing a copied example value from
    # crediting somebody else's provider account. Explicit custom memos remain
    # supported for accounts whose GGP dashboard supplies a different value.
    ggp_deposit_memo: str = ""
    # Toncenter v3 is used to bind one incoming transaction to one crypto order.
    # The API key is optional; without it the public endpoint is limited to 1 RPS.
    ton_api_base: str = "https://toncenter.com/api/v3"
    ton_api_key: str = ""
    # How often (seconds) the background worker scans TON for pending crypto orders.
    ton_poll_interval: int = 20
    # Keep matching for a short bounded window after the visible deadline/cancel
    # action. This absorbs wallet broadcast -> block/indexer lag without reusing an
    # invoice amount for another order.
    ton_reconciliation_grace_min: int = 10
    # Buyers get this long to pay a crypto order; after it, the order is auto-cancelled
    # (frees its TON amount and the buyer's one-pending-order slot). Shown to the buyer.
    crypto_payment_ttl_min: int = 60
    # Commercial TON price grid. Invoice uniqueness adds only 1 nanoton above this
    # rounded base, and issued amounts are never reused (including after expiry).
    ton_amount_jitter_step: Decimal = Decimal("0.001")

    # Test mode: skip WATA entirely. Orders get a "mark as paid" button instead of
    # a real payment link, so the referral/partner flows can be tested end-to-end.
    test_mode: bool = False

    # WATA limits the number of concurrent unpaid orders per buyer account. We
    # block a new order past this many recent unpaid ones with a clear message.
    max_active_orders: int = 3

    # Access control. Read as a plain string (a simple type pydantic-settings never
    # JSON-decodes) and expose the parsed list via the admin_ids property below.
    admin_ids_raw: str = Field(default="", validation_alias="admin_ids")

    # ── Public web API (for a website widget) ────────────────────
    # Off by default. Set the allowed origins (your site's exact domains) to enable
    # it: only browser requests from those origins are accepted (Origin check + CORS),
    # which is what actually restricts it to "your site only" for a client-side widget.
    # The optional IP allowlist is an extra gate (empty = off) for setups that proxy
    # the API through a fixed backend.
    public_api_allowed_origins_raw: str = Field(
        default="", validation_alias="public_api_allowed_origins"
    )
    public_api_allowed_ips_raw: str = Field(default="", validation_alias="public_api_allowed_ips")
    public_api_rate_limit: int = 20  # max requests per client IP per minute

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

    # Telegram updates (aiogram webhook). secret_token is always derived from
    # BOT_TOKEN and verified in X-Telegram-Bot-Api-Secret-Token.
    telegram_webhook_path: str = "/tg/webhook"

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
        """Require at least one configured payment rail."""
        if self.ggp_configured and not self.ggp_deposit_memo.strip():
            self.ggp_deposit_memo = self.ggp_username.strip()
        if not self.wata_enabled and not self.crypto_enabled:
            raise ValueError(
                "no payment rail configured: set WATA_TOKEN (card) or complete "
                "GGP_USERNAME, GGP_PASSWORD, and GGP_TON_ADDRESS (TON)"
            )
        return self

    @property
    def wata_enabled(self) -> bool:
        """Card rail is on when a WATA merchant token is configured."""
        return bool(self.wata_token)

    @property
    def crypto_enabled(self) -> bool:
        """Verified TON rail is on when all required GGP settings are configured."""
        return self.ggp_configured

    @property
    def ggp_configured(self) -> bool:
        """Whether all credentials required by the TON/GGP rail are present."""
        return bool(self.ggp_username and self.ggp_password and self.ggp_ton_address)

    @property
    def admin_ids(self) -> list[int]:
        """Admin Telegram IDs parsed from the comma-separated ADMIN_IDS env var."""
        return [int(part) for part in self.admin_ids_raw.replace(" ", "").split(",") if part]

    @property
    def public_api_allowed_origins(self) -> list[str]:
        """Exact site origins allowed to call the public API (Origin/CORS allowlist)."""
        raw = self.public_api_allowed_origins_raw
        return [o.strip().rstrip("/") for o in raw.split(",") if o.strip()]

    @property
    def public_api_allowed_ips(self) -> list[str]:
        """Optional extra IP allowlist for the public API (empty = disabled)."""
        return [ip.strip() for ip in self.public_api_allowed_ips_raw.split(",") if ip.strip()]

    @property
    def public_api_enabled(self) -> bool:
        """The public API is on only when at least one allowed origin is configured."""
        return bool(self.public_api_allowed_origins)

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
