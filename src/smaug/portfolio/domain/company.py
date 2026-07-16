"""Company identity — the registrant keys a ticker resolves to.

CVM open data is keyed by ``CD_CVM`` (the statements) and ``CNPJ`` (the FRE and
the DFP capital composition), never by the B3 trading ticker. This value object
carries exactly the keys the ingestion sources need, plus the CVM's own single
sector-of-activity, so a ticker outside the curated nine can be resolved on
demand instead of being hard-coded (``cvm_codes.py``).
"""

from __future__ import annotations

from dataclasses import dataclass, field

from smaug.portfolio.domain.share_classes import ShareClass


@dataclass(frozen=True)
class CompanyIdentity:
    """The CVM registrant keys and cadastral facts a B3 ticker maps to."""

    ticker: str
    cd_cvm: str  # no leading zeros — matches the statements' stripped ``CD_CVM``
    cnpj: str  # punctuated (``NN.NNN.NNN/NNNN-NN``) — matches the FRE's ``CNPJ``
    denom: str  # company name (``Nome_Empresarial``)
    cvm_sector: str  # CVM ``Setor_Atividade`` — a single, coarse activity label
    situation: str  # ``Situacao_Registro_CVM`` (e.g. "Ativo", "Cancelado")
    # The company's listed ON/PN classes (from the FCA securities member), whose
    # prices summed capitalize it (ADR 0014). Empty when the FCA lists no plain
    # ON/PN equity (e.g. a BDR- or unit-only line); the cap then stays null.
    share_classes: tuple[ShareClass, ...] = field(default_factory=tuple)
