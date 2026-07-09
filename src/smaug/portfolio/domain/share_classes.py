"""Which portfolio tickers trade as units rather than as a single share class.

A *unit* is a bundle of shares (SAPR11 = 1 ON + 2 PN), so its quoted price is
the price of the bundle while CVM's capital composition counts the underlying
shares. Dividing earnings by the share count would therefore yield a per-share
figure that does not line up with the per-unit price, so the per-share
indicators (LPA/VPA) are left null for these tickers until the bundle
composition is modelled. The multiples (P/L, P/VP) are unaffected — they are
computed from the market cap, never from a share count.
"""

from __future__ import annotations

UNIT_TICKERS: frozenset[str] = frozenset({"SAPR11", "TAEE11"})


def is_unit(ticker: str) -> bool:
    """True when ``ticker`` quotes a bundle of shares instead of one share."""
    return ticker in UNIT_TICKERS
