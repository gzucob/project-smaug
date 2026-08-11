"""Portfolio reference data: the sector fallback and resolved instrument kind.

Per-ticker facts (sector, listed classes, registrant keys, unit composition)
are resolved from the CVM FCA registry for every ticker, live — see
``test_company_registry.py`` — with no curated per-ticker map left anywhere
under ``src/`` (#212). What remains here is pure and dict-free: the coarse
CVM-label fallback and predicates over an FCA-resolved identity.
"""

from smaug.portfolio.domain.company import CompanyIdentity, InstrumentKind, is_unit
from smaug.portfolio.domain.sectors import Sector, sector_from_cvm


def test_sector_from_cvm_folds_the_activity_label_to_the_enum() -> None:
    # The coarse fallback for any ticker: the CVM's single activity label
    # mapped to the five-value enum (accent- and case-insensitive).
    assert sector_from_cvm("Bancos") is Sector.BANK
    assert sector_from_cvm("Seguradoras") is Sector.INSURER
    assert sector_from_cvm("Energia Elétrica") is Sector.UTILITY
    assert sector_from_cvm("Petróleo e Gás") is Sector.COMMODITY
    assert sector_from_cvm("Extração Mineral") is Sector.COMMODITY
    # Anything unmatched degrades to INDUSTRY, never raises (e.g. Klabin).
    assert sector_from_cvm("Papel e Celulose") is Sector.INDUSTRY
    assert sector_from_cvm("") is Sector.INDUSTRY


def _identity(ticker: str, kind: InstrumentKind) -> CompanyIdentity:
    return CompanyIdentity(
        ticker=ticker,
        cd_cvm="1",
        cnpj="00.000.000/0001-00",
        denom="TEST S.A.",
        cvm_sector="Outros",
        situation="Ativo",
        instrument_kind=kind,
        instrument_type="fixture",
    )


def test_is_unit_reads_the_resolved_instrument_kind() -> None:
    assert is_unit(_identity("SAPR11", InstrumentKind.UNIT))
    assert not is_unit(_identity("BEEF11", InstrumentKind.SUBSCRIPTION_WARRANT))
    assert not is_unit(_identity("WEGE3", InstrumentKind.COMMON_SHARE))
