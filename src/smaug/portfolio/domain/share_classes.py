"""Which share classes each portfolio ticker's company actually lists on B3.

A Brazilian company can list more than one class of the same equity: ordinary
shares (ON, ticker ending in ``3``), preferred shares (PN, ending in ``4``), and
sometimes a *unit* (ending in ``11``) that bundles both. **Each class trades at
its own price** — they are not interchangeable, and a unit is not the clean sum
of its parts.

This is why the market capitalization cannot be ``one quote × every share``: the
company is worth the sum of its classes, each priced on its own quote:

    cap = Σ over listed classes (class price × shares filed for that class)

That identity is what ``LISTED_CLASSES`` below exists to serve (ADR 0014). It
also lets a unit be capitalized without knowing its bundle composition, since
summing class by class never mentions the bundle.

The bundle composition itself is still unmodelled, so the per-share indicators
(LPA/VPA) stay null for a unit — dividing earnings by the underlying share count
would not line up with the per-unit price (#38). ``is_unit`` marks those tickers.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


class ShareKind(StrEnum):
    """A class of shares, as CVM's filed capital composition splits them."""

    COMMON = "common"  # ON — ticker ends in 3
    PREFERRED = "preferred"  # PN — ticker ends in 4


@dataclass(frozen=True, slots=True)
class ShareClass:
    """One listed class of a company's equity: the symbol it trades under."""

    symbol: str
    kind: ShareKind


def _on(symbol: str) -> ShareClass:
    return ShareClass(symbol=symbol, kind=ShareKind.COMMON)


def _pn(symbol: str) -> ShareClass:
    return ShareClass(symbol=symbol, kind=ShareKind.PREFERRED)


# The classes each portfolio ticker's company lists — including the ticker's own
# siblings. PETR4 is analyzed, but Petrobras is worth PETR3 + PETR4; SAPR11 is a
# unit, and Sanepar is worth SAPR3 + SAPR4. A company with a single listed class
# maps to just itself. Classes 5/6 (PNA/PNB) do not occur in this portfolio (#72).
LISTED_CLASSES: dict[str, tuple[ShareClass, ...]] = {
    "PETR4": (_on("PETR3"), _pn("PETR4")),
    "VALE3": (_on("VALE3"),),
    "SAPR11": (_on("SAPR3"), _pn("SAPR4")),
    "TAEE11": (_on("TAEE3"), _pn("TAEE4")),
    "WEGE3": (_on("WEGE3"),),
    "BBAS3": (_on("BBAS3"),),
    "BBDC4": (_on("BBDC3"), _pn("BBDC4")),
    "BBSE3": (_on("BBSE3"),),
    "CXSE3": (_on("CXSE3"),),
}

# How many underlying shares each unit bundles, by class (ON, PN). Curated
# reference data — the FRE does not publish it (like ``TICKER_TO_CVM_CODE``). Both
# portfolio units bundle 1 ON + 2 PN, so a unit is worth three underlying shares.
# The per-*unit* earnings/book value the market pairs with the unit price is the
# per-share figure times this bundle size, which is why LPA/VPA divide the filed
# count by it. This reconciles with the per-class LPA the DRE files at 3.99 (#38):
# TAEE11 files ON = PN = R$1.639, so per unit = 3 × 1.639 = R$4.92.
UNIT_COMPOSITION: dict[str, tuple[int, int]] = {  # (ON per unit, PN per unit)
    "SAPR11": (1, 2),
    "TAEE11": (1, 2),
}

UNIT_TICKERS: frozenset[str] = frozenset(UNIT_COMPOSITION)


def shares_per_unit(ticker: str) -> int | None:
    """Underlying shares one unit of ``ticker`` bundles (``None`` when not a unit)."""
    composition = UNIT_COMPOSITION.get(ticker)
    return None if composition is None else sum(composition)


def listed_classes(ticker: str) -> tuple[ShareClass, ...]:
    """The share classes whose prices, summed, capitalize ``ticker``'s company.

    Empty for a ticker outside the portfolio: the caller degrades the market cap
    to null rather than guessing a composition.
    """
    return LISTED_CLASSES.get(ticker, ())


def is_unit(ticker: str) -> bool:
    """True when ``ticker`` quotes a bundle of shares instead of one class."""
    return ticker in UNIT_TICKERS
