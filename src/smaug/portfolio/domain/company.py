"""Company identity — the registrant keys a ticker resolves to.

CVM open data is keyed by ``CD_CVM`` (the statements) and ``CNPJ`` (the FRE and
the DFP capital composition), never by the B3 trading ticker. This value object
carries exactly the keys the ingestion sources need, plus the CVM's own single
sector-of-activity, resolved for every ticker from the CVM FCA registry — no
ticker is hard-coded (#212; ``CvmCompanyRegistry``).
"""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import date
from enum import StrEnum

from smaug.portfolio.domain.share_classes import (
    PerShareClass,
    ShareClass,
    ShareClassMapping,
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


# B3 security codes use a four-character alphanumeric root followed by one or
# two class digits.  FCA carries real organized-market roots with more than one
# digit (for example B100), so syntax alone cannot decide whether a code-shaped
# row is listed.  ``fundamental_exclusion`` makes that decision from FCA/B3
# market evidence below.
_TRADING_CODE = re.compile(r"^[A-Z0-9]{4}[0-9]{1,2}$")


def is_trading_code(ticker: str) -> bool:
    """Whether ``ticker`` has the syntax of a B3 security code."""
    code = ticker.strip().upper()
    return bool(_TRADING_CODE.fullmatch(code)) and not code.isdigit()


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
    # The FCA venue fields are identity evidence, not a replacement for B3
    # tape evidence. ``Mercado`` distinguishes B3's organized markets from
    # non-organized OTC rows that can look like ordinary trading codes.
    market: str = ""
    venue: str = ""
    # Source labels proving that a recovered code crossed the strict B3 detail,
    # supplement and COTAHIST chain. Normal FCA rows carry only their market
    # evidence; recovery may use this to override an incomplete venue field.
    listing_evidence: tuple[str, ...] = field(default_factory=tuple)
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
    # Same-priority FCA rows for one ticker must not silently choose whichever
    # CNPJ happened to appear first in the CSV. The registry keeps the selected
    # identity for diagnosis, but this field makes the ambiguity explicit so
    # callers can exclude it from analysis and the listed universe.
    ambiguous_cnpjs: tuple[str, ...] = field(default_factory=tuple)
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
    # Current FCA identity evidence for each economic class. It is provenance,
    # not an alternative identity key, so older value-object callers may omit it.
    share_class_mappings: tuple[ShareClassMapping, ...] = field(
        default_factory=tuple, compare=False
    )


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
    if not is_trading_code(identity.ticker):
        return f"FCA trading code has invalid syntax: {identity.ticker!r}"
    if identity.ambiguous_cnpjs:
        candidates = ", ".join(identity.ambiguous_cnpjs)
        return f"ambiguous ticker-to-CNPJ mapping ({candidates})"
    if identity.trading_ended is not None:
        return f"trading ended on {identity.trading_ended.isoformat()}"
    if identity.instrument_kind not in _FUNDAMENTAL_INSTRUMENTS:
        label = identity.instrument_type or identity.instrument_kind.value
        return f"FCA instrument type is {label!r}"
    market_is_organized = is_organized_market(identity.market, identity.venue)
    if not market_is_organized and not _has_b3_evidence(identity):
        market = identity.market or "<blank>"
        venue = identity.venue or "<blank>"
        return (
            "FCA security is outside an organized B3 market "
            f"(market={market!r}, venue={venue!r})"
        )
    return None


def is_organized_market(market: str, venue: str) -> bool:
    """Whether FCA market and administrator identify an organized B3 venue."""
    market_key = _fold(market)
    venue_key = _fold(venue)
    return market_key in {"bolsa", "balcao organizado"} and venue_key in {
        "b3",
        "b3 sa",
    }


def _has_b3_evidence(identity: CompanyIdentity) -> bool:
    """Whether a recovered identity crossed every official B3 source boundary."""
    required = {"b3.get_detail", "b3.listed_supplement", "b3.cotahist"}
    return required.issubset(identity.listing_evidence)


def _fold(text: str) -> str:
    """Fold the FCA's accent-bearing market labels for comparison."""
    decomposed = unicodedata.normalize("NFKD", text)
    return (
        "".join(c for c in decomposed if not unicodedata.combining(c)).strip().lower()
    )
