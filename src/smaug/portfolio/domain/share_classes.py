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
``Composicao_BDR_Unit`` text (#212). It aligns the closing-share denominator of
VPA with the per-unit price; its class-preserving sibling ``unit_components``
composes the issuer's filed CPC 41 results into LPA per unit (ADR 0054). Whether
a code is a unit comes from that resolved identity, never from its numeric
suffix (ADR 0053).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum


class ShareKind(StrEnum):
    """A class of shares, as CVM's filed capital composition splits them."""

    COMMON = "common"  # ON — ticker ends in 3
    PREFERRED = "preferred"  # PN — ticker ends in 4, 5 or 6


class PerShareClass(StrEnum):
    """The class labels a CVM CPC 41 disclosure assigns results to."""

    ORDINARY = "ON"
    PREFERRED = "PN"
    PREFERRED_A = "PNA"
    PREFERRED_B = "PNB"


@dataclass(frozen=True, slots=True)
class ShareClass:
    """One listed class of a company's equity: the symbol it trades under."""

    symbol: str
    kind: ShareKind
    # FCA's capital total groups every preferred subclass under ``preferred``.
    # The B3 class number preserves the finer PNA/PNB identity needed to pair a
    # quote with the matching row from FRE's class ledger (#72).
    per_share_class: PerShareClass = field(init=False)

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "per_share_class",
            per_share_class_from_symbol(self.symbol, self.kind),
        )


@dataclass(frozen=True, slots=True)
class UnitComponent:
    """One underlying class and quantity declared in an FCA unit composition."""

    quantity: int
    per_share_class: PerShareClass
    symbol: str | None = None


def per_share_class_from_symbol(symbol: str, kind: ShareKind) -> PerShareClass:
    """Resolve ON/PN/PNA/PNB for a plain share symbol.

    FCA identifies the instrument as ordinary/preferred; the B3 class number
    distinguishes the preferred subclasses that CPC 41 reports separately.
    """
    if kind is ShareKind.COMMON:
        return PerShareClass.ORDINARY
    suffix = symbol.upper().strip()[4:]
    if suffix == "5":
        return PerShareClass.PREFERRED_A
    if suffix == "6":
        return PerShareClass.PREFERRED_B
    return PerShareClass.PREFERRED
