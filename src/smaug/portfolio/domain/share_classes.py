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


class ShareClassMappingStatus(StrEnum):
    """Whether CVM evidence identifies one economic class unambiguously."""

    RESOLVED = "resolved"
    UNRESOLVED = "unresolved"
    NOT_YET_LISTED = "not_yet_listed"


class EconomicRightsStatus(StrEnum):
    """Whether the filing evidence identifies the class's economic rights."""

    RESOLVED = "resolved"
    UNRESOLVED = "unresolved"


@dataclass(frozen=True, slots=True)
class TickerCodeEvidence:
    """One CVM-filed trading code and the FCA years in which it appeared."""

    symbol: str
    filed_years: tuple[int, ...] = ()
    source: str = "cvm_fca"


@dataclass(frozen=True, slots=True)
class ShareClassMapping:
    """Stable economic-class identity with its code and filing evidence.

    ``class_id`` is keyed by the CVM registrant and the economic class, not by
    the current ticker. This is what lets a rename retain one identity while a
    merger's extinguished class remains separate from its survivor.
    """

    class_id: str
    symbol: str | None
    kind: ShareKind | None
    per_share_class: PerShareClass | None
    status: ShareClassMappingStatus = ShareClassMappingStatus.RESOLVED
    economic_rights: EconomicRightsStatus = EconomicRightsStatus.RESOLVED
    code_evidence: tuple[TickerCodeEvidence, ...] = ()
    evidence: tuple[str, ...] = ()


def share_class_id(cnpj: str, per_share_class: PerShareClass) -> str:
    """Build the stable CVM-registrant/economic-class key."""
    return f"{cnpj}:{per_share_class.value}"


def mapping_for_share_class(
    cnpj: str,
    share_class: ShareClass,
    *,
    code_evidence: tuple[TickerCodeEvidence, ...] = (),
) -> ShareClassMapping:
    """Build resolved mapping evidence for one listed class."""
    return ShareClassMapping(
        class_id=share_class_id(cnpj, share_class.per_share_class),
        symbol=share_class.symbol,
        kind=share_class.kind,
        per_share_class=share_class.per_share_class,
        code_evidence=code_evidence,
        evidence=("cvm_fca.share_class",),
    )


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
