"""Deterministic ticker -> sector map (fixed reference data).

This is a hard-coded de/para, never a semantic inference. Phase 2 will use
the sector to pick the right set of criteria; Phase 1 uses it only to drive
the sector-directed completeness check (plan §6).
"""

from __future__ import annotations

from collections.abc import Iterable
from enum import StrEnum

from smaug.shared.errors import UnknownTickerError


class Sector(StrEnum):
    """Business sector of a ticker, granular enough for Phase 2 criteria."""

    BANK = "bank"
    INSURER = "insurer"
    UTILITY = "utility"
    COMMODITY = "commodity"
    INDUSTRY = "industry"


# The target portfolio (plan §1). Order is stable for reproducible collection.
PORTFOLIO: dict[str, Sector] = {
    "PETR4": Sector.COMMODITY,
    "VALE3": Sector.COMMODITY,
    "SAPR11": Sector.UTILITY,
    "TAEE11": Sector.UTILITY,
    "WEGE3": Sector.INDUSTRY,
    "BBAS3": Sector.BANK,
    "BBDC4": Sector.BANK,
    "BBSE3": Sector.INSURER,
    "CXSE3": Sector.INSURER,
}


def portfolio_tickers() -> tuple[str, ...]:
    """Return the portfolio tickers in a stable order."""
    return tuple(PORTFOLIO.keys())


def sector_of(ticker: str) -> Sector:
    """Return the sector for ``ticker``, raising ``UnknownTickerError`` if unknown."""
    try:
        return PORTFOLIO[ticker]
    except KeyError as exc:
        raise UnknownTickerError(ticker) from exc


def require_portfolio_tickers(tickers: Iterable[str]) -> None:
    """Reject the first ticker not in the portfolio, raising ``UnknownTickerError``.

    A ticker absent from the portfolio is a user input error the moment it is
    passed — not a data-source 404. Ingest stays batch-resilient for real
    filings, but a typo is caught up front (like the analyze path, #13/#60)
    instead of being folded into the collection log as an expected skip.
    """
    for ticker in tickers:
        if ticker not in PORTFOLIO:
            raise UnknownTickerError(ticker)
