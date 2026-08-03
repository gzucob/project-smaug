"""Logging setup. Not sophisticated — it just needs to exist (plan §5)."""

from __future__ import annotations

import logging

_CONFIGURED = False


def configure_logging(level: int = logging.INFO) -> None:
    """Configure root logging once, idempotently."""
    global _CONFIGURED
    if _CONFIGURED:
        return
    logging.basicConfig(
        level=level,
        format="%(asctime)s %(levelname)-7s %(name)s | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    # httpx logs a line per request at INFO. That used to matter because the URL
    # carried a token; no source needs one now (ADR 0041), but a whole-exchange
    # run still makes thousands of calls, and the collection log is the thing
    # meant to be read — so keep httpx quiet below WARNING.
    logging.getLogger("httpx").setLevel(logging.WARNING)
    _CONFIGURED = True


def get_logger(name: str) -> logging.Logger:
    """Return a named logger, ensuring logging is configured first."""
    configure_logging()
    return logging.getLogger(name)
