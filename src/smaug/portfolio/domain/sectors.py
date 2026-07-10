"""Deterministic ticker -> sector map (fixed reference data).

This is a hard-coded de/para, never a semantic inference. Phase 2 will use
the sector to pick the right set of criteria; Phase 1 uses it only to drive
the sector-directed completeness check (plan §6).
"""

from __future__ import annotations

from enum import StrEnum

from smaug.shared.errors import UnknownTickerError


class Sector(StrEnum):
    """Business sector of a ticker, granular enough for Phase 2 criteria."""

    BANK = "bank"
    INSURER = "insurer"
    UTILITY = "utility"
    COMMODITY = "commodity"
    INDUSTRY = "industry"

    @property
    def is_financial(self) -> bool:
        """Banks and insurers report under a financial-institution structure."""
        return self in _FINANCIAL_SECTORS


_FINANCIAL_SECTORS: frozenset[Sector] = frozenset({Sector.BANK, Sector.INSURER})


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
