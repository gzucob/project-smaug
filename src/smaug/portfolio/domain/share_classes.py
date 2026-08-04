"""Which share classes a company lists, and the shapes B3 tickers take.

A Brazilian company can list more than one class of the same equity: ordinary
shares (ON, ticker ending in ``3``), preferred shares (PN, ending in ``4``,
``5``, or ``6``), and sometimes a *unit* (ending in ``11``) that bundles both.
**Each class trades at its own price** — they are not interchangeable, and a
unit is not the clean sum of its parts.

This is why the market capitalization cannot be ``one quote × every share``:
the company is worth the sum of its classes, each priced on its own quote:

    cap = Σ over listed classes (class price × shares filed for that class)

That identity is what ``CompanyIdentity.share_classes`` exists to serve (ADR
0014, resolved from the CVM FCA registry — ``CvmCompanyRegistry``). It also
lets a unit be capitalized without knowing its bundle composition, since
summing class by class never mentions the bundle.

The bundle composition itself — how many underlying shares one unit is worth —
is what ``CompanyIdentity.shares_per_unit`` carries, parsed from the FCA's own
``Composicao_BDR_Unit`` text (#212). Without it the per-share indicators
(LPA/VPA) stay null for a unit — dividing earnings by the underlying share
count would not line up with the per-unit price (#38). ``is_unit`` marks those
tickers by their B3 suffix, a shape test rather than a lookup.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


class ShareKind(StrEnum):
    """A class of shares, as CVM's filed capital composition splits them."""

    COMMON = "common"  # ON — ticker ends in 3
    PREFERRED = "preferred"  # PN — ticker ends in 4, 5 or 6


@dataclass(frozen=True, slots=True)
class ShareClass:
    """One listed class of a company's equity: the symbol it trades under."""

    symbol: str
    kind: ShareKind


def is_unit(ticker: str) -> bool:
    """True when ``ticker``'s class suffix (``11``) names a unit, not one class.

    A shape test on the B3 trading code — the same convention
    ``portfolio.domain.universe`` and ``cvm_registry._kind_from_suffix`` already
    encode (3/4/5/6 for a plain class, 11 for a unit) — not a per-ticker lookup,
    so it holds for any unit B3 lists, not just a hand-picked few.
    """
    return ticker[4:] == "11"
