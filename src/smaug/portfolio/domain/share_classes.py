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

UNIT_TICKERS: frozenset[str] = frozenset({"SAPR11", "TAEE11"})


def listed_classes(ticker: str) -> tuple[ShareClass, ...]:
    """The share classes whose prices, summed, capitalize ``ticker``'s company.

    Empty for a ticker outside the portfolio: the caller degrades the market cap
    to null rather than guessing a composition.
    """
    return LISTED_CLASSES.get(ticker, ())


def is_unit(ticker: str) -> bool:
    """True when ``ticker`` quotes a bundle of shares instead of one class."""
    return ticker in UNIT_TICKERS
