"""Company identity — the registrant keys a ticker resolves to.

CVM open data is keyed by ``CD_CVM`` (the statements) and ``CNPJ`` (the FRE and
the DFP capital composition), never by the B3 trading ticker. This value object
carries exactly the keys the ingestion sources need, plus the CVM's own single
sector-of-activity, so a ticker outside the curated nine can be resolved on
demand instead of being hard-coded (``cvm_codes.py``).
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import date

from smaug.portfolio.domain.share_classes import ShareClass

# Ticker -> the registrant that files for it (``CD_CVM``), or ``None`` when the
# ticker resolves nowhere. The mirror is keyed on the registrant (ADR 0030), so
# every reader of it takes one of these — curated for the nine, registry-backed
# for the rest, exactly like ``SectorResolver``.
RegistrantResolver = Callable[[str], str | None]


@dataclass(frozen=True)
class CompanyIdentity:
    """The CVM registrant keys and cadastral facts a B3 ticker maps to."""

    ticker: str
    cd_cvm: str  # no leading zeros — matches the statements' stripped ``CD_CVM``
    cnpj: str  # punctuated (``NN.NNN.NNN/NNNN-NN``) — matches the FRE's ``CNPJ``
    denom: str  # company name (``Nome_Empresarial``)
    cvm_sector: str  # CVM ``Setor_Atividade`` — a single, coarse activity label
    situation: str  # ``Situacao_Registro_CVM`` (e.g. "Ativo", "Cancelado")
    # When this security was admitted to listing, per the FCA's
    # ``Data_Inicio_Listagem`` — the registrant's record, not a vendor's. It tells
    # a year that precedes the instrument apart from a year a price source merely
    # has no history for (#153); see ``portfolio.domain.listings`` for why this
    # column and not its neighbour.
    listed_since: date | None = None
    # The company's listed ON/PN classes (from the FCA securities member), whose
    # prices summed capitalize it (ADR 0014). Empty when the FCA lists no plain
    # ON/PN equity (e.g. a BDR- or unit-only line); the cap then stays null.
    share_classes: tuple[ShareClass, ...] = field(default_factory=tuple)
