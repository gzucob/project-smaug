"""Deterministic ticker -> sector map (fixed reference data).

This is a hard-coded de/para, never a semantic inference. Phase 2 will use
the sector to pick the right set of criteria; Phase 1 uses it only to drive
the sector-directed completeness check (plan §6).

``PORTFOLIO`` is a curated sector *fallback/override* for these nine — it is
no longer the portfolio-membership list (#151 moved that to Postgres,
``portfolio.infrastructure.sql_repository``); a ticker outside this dict
still resolves a sector via ``sector_from_cvm``, curated or not.
"""

from __future__ import annotations

import unicodedata
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


def sector_of(ticker: str) -> Sector:
    """Return the sector for ``ticker``, raising ``UnknownTickerError`` if unknown."""
    try:
        return PORTFOLIO[ticker]
    except KeyError as exc:
        raise UnknownTickerError(ticker) from exc


def _fold(text: str) -> str:
    """Uppercase and strip accents, so substring matches survive 'ç', 'ã', etc."""
    decomposed = unicodedata.normalize("NFKD", text)
    return "".join(c for c in decomposed if not unicodedata.combining(c)).upper()


def sector_from_cvm(cvm_sector: str) -> Sector:
    """Best-effort ``Sector`` from the CVM ``Setor_Atividade`` label.

    A coarse fallback for tickers outside the curated nine: the CVM's single
    activity label (e.g. "Papel e Celulose") folded to the five-value enum. It
    only has to be good enough to seed the display sector and the regime
    *fallback* — indicator applicability is decided by the ``filed_regime`` read
    off the statement itself (ADR 0015), not by this. The real B3 taxonomy
    replaces the enum in a follow-up (M2 taxonomy slice).
    """
    label = _fold(cvm_sector)
    if "BANCO" in label or "INTERMEDIACAO FINANCEIRA" in label:
        return Sector.BANK
    if "SEGUR" in label or "PREVIDENCIA" in label or "CAPITALIZACAO" in label:
        return Sector.INSURER
    # "GAS" is deliberately absent: it collides with "Petróleo e Gás" (a
    # commodity), and gas distribution is rare enough to leave to the default.
    if any(k in label for k in ("ENERGIA ELETRICA", "SANEAMENTO", "AGUA")):
        return Sector.UTILITY
    if any(
        k in label
        for k in ("PETROLEO", "MINERA", "EXTRACAO", "SIDERURGIA", "METALURGIA")
    ):
        return Sector.COMMODITY
    return Sector.INDUSTRY
