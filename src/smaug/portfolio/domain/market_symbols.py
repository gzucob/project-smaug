"""Ticker → market-data vendor symbol overrides (renames / delistings).

A B3 ticker usually maps to its price-history symbol by identity — the vendor
adapter appends its own suffix (Yahoo/EODHD take ``PETR4`` → ``PETR4.SA``). But a
company that changed its trading code, merged, or delisted may have its history
under a *different* symbol on the vendor, so its own ticker resolves to nothing
(#64). This map records those exceptions, next to the portfolio's other
ticker → external-key maps (``cvm_codes.py``).

Empty by default: the curated nine are all currently listed under their own codes
(ADR 0011). An entry is added when a rename/delisting is discovered — the value is
the vendor **stem** (no ``.SA`` suffix), which each adapter decorates for its own
vendor.
"""

from __future__ import annotations

# ticker -> the market-data symbol stem to query instead of the ticker itself.
# Example (illustrative, not a live entry):
#     "OLDX3": "NEWX3",  # renamed on B3 in 20XX; Yahoo history lives under NEWX3.SA
TICKER_SYMBOL_OVERRIDES: dict[str, str] = {}


def market_symbol(ticker: str) -> str:
    """The vendor symbol stem for ``ticker`` — the ticker itself unless overridden."""
    return TICKER_SYMBOL_OVERRIDES.get(ticker, ticker)
