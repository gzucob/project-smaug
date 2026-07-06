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
    # httpx logs each request line at INFO — and that line includes the full URL,
    # i.e. the ``?token=`` query param. The repo is public and the token must
    # never be printed (CLAUDE.md), so keep httpx quiet below WARNING.
    logging.getLogger("httpx").setLevel(logging.WARNING)
    _CONFIGURED = True


def get_logger(name: str) -> logging.Logger:
    """Return a named logger, ensuring logging is configured first."""
    configure_logging()
    return logging.getLogger(name)
