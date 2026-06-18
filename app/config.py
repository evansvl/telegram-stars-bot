"""Application configuration loaded from environment / .env via pydantic-settings."""

from __future__ import annotations

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

    # Access control
    admin_ids: list[int] = Field(default_factory=list)

    # Infrastructure
    database_url: str = "postgresql+asyncpg://bot:bot@postgres:5432/wata_bot"
    redis_url: str = "redis://redis:6379/0"

    # Webhook (HTTP behind a user-managed reverse proxy)
    webhook_host: str = ""
    webhook_port: int = 8080
    webhook_path: str = "/wata/webhook"
    webhook_secret: str = ""

    log_level: str = "INFO"

    @field_validator("admin_ids", mode="before")
    @classmethod
    def _parse_admin_ids(cls, value: object) -> object:
        """Accept a comma-separated string of admin IDs from the environment."""
        if isinstance(value, str):
            return [int(part) for part in value.replace(" ", "").split(",") if part]
        return value

    @field_validator("markup_percent")
    @classmethod
    def _validate_markup(cls, value: float) -> float:
        if value < 0:
            raise ValueError("MARKUP_PERCENT must be non-negative")
        return value

    @property
    def webhook_url(self) -> str:
        """Full public URL that must be registered in the WATA dashboard."""
        host = self.webhook_host.rstrip("/")
        return f"https://{host}{self.webhook_path}"

    def is_admin(self, tg_id: int) -> bool:
        return tg_id in self.admin_ids


_settings: Settings | None = None


def get_settings() -> Settings:
    """Return a cached Settings instance (lazy singleton)."""
    global _settings
    if _settings is None:
        _settings = Settings()  # type: ignore[call-arg]
    return _settings
