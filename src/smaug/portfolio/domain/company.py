"""Company identity — the registrant keys a ticker resolves to.

CVM open data is keyed by ``CD_CVM`` (the statements) and ``CNPJ`` (the FRE and
the DFP capital composition), never by the B3 trading ticker. This value object
carries exactly the keys the ingestion sources need, plus the CVM's own single
sector-of-activity, resolved for every ticker from the CVM FCA registry — no
ticker is hard-coded (#212; ``CvmCompanyRegistry``).
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import date
from enum import StrEnum

from smaug.portfolio.domain.share_classes import (
    PerShareClass,
    ShareClass,
    ShareKind,
    UnitComponent,
    per_share_class_from_symbol,
)

# Ticker -> the registrant that files for it (``CD_CVM``), or ``None`` when the
# ticker resolves nowhere. The mirror is keyed on the registrant (ADR 0030), so
# every reader of it takes one of these — registry-backed, unconditionally
# (#212), exactly like ``SectorResolver``.
RegistrantResolver = Callable[[str], str | None]


class InstrumentKind(StrEnum):
    """The security type the FCA assigns to one trading code."""

    COMMON_SHARE = "common_share"
    PREFERRED_SHARE = "preferred_share"
    UNIT = "unit"
    SUBSCRIPTION_WARRANT = "subscription_warrant"
    SUBSCRIPTION_RECEIPT = "subscription_receipt"
    DEPOSITARY_RECEIPT = "depositary_receipt"
    OTHER = "other"


_FUNDAMENTAL_INSTRUMENTS = frozenset(
    {
        InstrumentKind.COMMON_SHARE,
        InstrumentKind.PREFERRED_SHARE,
        InstrumentKind.UNIT,
    }
)


@dataclass(frozen=True)
class CompanyIdentity:
    """The CVM registrant keys and cadastral facts a B3 ticker maps to."""

    ticker: str
    cd_cvm: str  # no leading zeros — matches the statements' stripped ``CD_CVM``
    cnpj: str  # punctuated (``NN.NNN.NNN/NNNN-NN``) — matches the FRE's ``CNPJ``
    denom: str  # company name (``Nome_Empresarial``)
    cvm_sector: str  # CVM ``Setor_Atividade`` — a single, coarse activity label
    situation: str  # ``Situacao_Registro_CVM`` (e.g. "Ativo", "Cancelado")
    instrument_kind: InstrumentKind
    # The FCA's own ``Valor_Mobiliario`` label, kept verbatim so rejecting a
    # non-equity code can name what the regulator says it is.
    instrument_type: str
    # End of this security's trading interval. ``None`` means the FCA row is
    # current; a dated identity remains resolvable for an explicit diagnosis but
    # does not enter the listed-equity universe.
    trading_ended: date | None = None
    # When this security was admitted to listing, per the FCA's
    # ``Data_Inicio_Listagem`` — the registrant's record, not a vendor's. It tells
    # a year that precedes the instrument apart from a year a price source merely
    # has no history for (#153); its neighbour ``Data_Inicio_Negociacao`` is the
    # start of trading in the *current* listing segment, not the instrument's
    # debut, which is why this column and not that one.
    listed_since: date | None = None
    # The company's listed ON/PN classes (from the FCA securities member), whose
    # prices summed capitalize it (ADR 0014). Empty when the FCA lists no plain
    # ON/PN equity (e.g. a BDR- or unit-only line); the cap then stays null.
    share_classes: tuple[ShareClass, ...] = field(default_factory=tuple)
    # Underlying shares one unit of *this* ticker bundles, parsed from the FCA's
    # own ``Composicao_BDR_Unit`` text (e.g. "1 KLBN3 + 4 KLBN4" -> 5). ``None``
    # for a non-unit or an unreadable unit composition. The explicit
    # ``instrument_kind`` distinguishes those cases so the latter becomes a
    # named null instead of falling back to the filed underlying total.
    shares_per_unit: int | None = None
    # The same FCA bundle with its class quantities intact. ``shares_per_unit``
    # answers the denominator question; these components answer the different
    # CPC 41 question of how much ON/PN/PNA/PNB result one unit carries.
    unit_components: tuple[UnitComponent, ...] = field(default_factory=tuple)


UnitResolver = Callable[[str], bool]


def is_unit(identity: CompanyIdentity) -> bool:
    """Whether the resolved FCA identity is a unit."""
    return identity.instrument_kind is InstrumentKind.UNIT


def no_units(_ticker: str) -> bool:
    """The default resolver when no FCA identity map was wired."""
    return False


def per_share_components(identity: CompanyIdentity) -> tuple[UnitComponent, ...]:
    """The CPC 41 class or bundle represented by one resolved security."""
    if identity.instrument_kind is InstrumentKind.UNIT:
        return identity.unit_components
    if identity.instrument_kind is InstrumentKind.COMMON_SHARE:
        return (UnitComponent(1, PerShareClass.ORDINARY, identity.ticker),)
    if identity.instrument_kind is InstrumentKind.PREFERRED_SHARE:
        return (
            UnitComponent(
                1,
                per_share_class_from_symbol(
                    identity.ticker,
                    # A preferred identity is positive FCA evidence; the helper
                    # uses only the suffix to distinguish A/B subclasses.
                    ShareKind.PREFERRED,
                ),
                identity.ticker,
            ),
        )
    return ()


def fundamental_exclusion(identity: CompanyIdentity) -> str | None:
    """Why an FCA security cannot enter fundamental analysis, if anything."""
    if identity.trading_ended is not None:
        return f"trading ended on {identity.trading_ended.isoformat()}"
    if identity.instrument_kind not in _FUNDAMENTAL_INSTRUMENTS:
        label = identity.instrument_type or identity.instrument_kind.value
        return f"FCA instrument type is {label!r}"
    return None
