"""Structured (key=value) logging configuration."""

from __future__ import annotations

import logging
import sys


class KeyValueFormatter(logging.Formatter):
    """Compact, grep-friendly structured log lines."""

    def format(self, record: logging.LogRecord) -> str:
        base = (
            f"ts={self.formatTime(record, '%Y-%m-%dT%H:%M:%S%z')} "
            f"level={record.levelname} "
            f"logger={record.name} "
            f"msg={record.getMessage()!r}"
        )
        extras = getattr(record, "extra_fields", None)
        if extras:
            base += " " + " ".join(f"{k}={v!r}" for k, v in extras.items())
        if record.exc_info:
            base += "\n" + self.formatException(record.exc_info)
        return base


def setup_logging(level: str = "INFO") -> None:
    """Configure root logging once at process start."""
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(KeyValueFormatter())

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
