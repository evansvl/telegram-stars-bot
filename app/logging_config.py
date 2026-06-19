"""Structured (key=value) logging configuration."""

from __future__ import annotations

import logging
import sys


class ReadableFormatter(logging.Formatter):
    """Human-readable, aligned log lines for `docker compose logs`.

    Example: ``2026-06-18 23:30:00 INFO     app.main: telegram webhook set ...``
    Structured extras (via ``log_extra``) are appended as ``key=value``.
    """

    def format(self, record: logging.LogRecord) -> str:
        ts = self.formatTime(record, "%Y-%m-%d %H:%M:%S")
        base = f"{ts} {record.levelname:<8} {record.name}: {record.getMessage()}"
        extras = getattr(record, "extra_fields", None)
        if extras:
            base += " " + " ".join(f"{k}={v!r}" for k, v in extras.items())
        if record.exc_info:
            base += "\n" + self.formatException(record.exc_info)
        return base


def setup_logging(level: str = "INFO") -> None:
    """Configure root logging once at process start."""
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(ReadableFormatter())

    root = logging.getLogger()
    root.handlers.clear()
    root.addHandler(handler)
    root.setLevel(level.upper())

    # Tame noisy third-party loggers.
    logging.getLogger("aiogram.event").setLevel(logging.WARNING)
    logging.getLogger("aiohttp.access").setLevel(logging.WARNING)


def log_extra(**fields: object) -> dict[str, object]:
    """Helper to attach structured fields to a log record.

    Usage: ``logger.info("order created", extra=log_extra(order_id=oid))``
    """
    return {"extra_fields": fields}
