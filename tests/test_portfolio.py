"""Portfolio reference data: the sector fallback and the unit shape test.

Per-ticker facts (sector, listed classes, registrant keys, unit composition)
are resolved from the CVM FCA registry for every ticker, live — see
``test_company_registry.py`` — with no curated per-ticker map left anywhere
under ``src/`` (#212). What remains here is pure and dict-free: the coarse
CVM-label fallback, and the B3 ticker-suffix shape test for a unit.
"""

from smaug.portfolio.domain.sectors import Sector, sector_from_cvm
from smaug.portfolio.domain.share_classes import is_unit


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


def test_is_unit_reads_the_ticker_suffix() -> None:
    # A shape test on the B3 trading code, not a lookup — holds for any unit,
    # not only the ones a fixture happens to name.
    assert is_unit("SAPR11")
    assert is_unit("KLBN11")
    assert not is_unit("WEGE3")
    assert not is_unit("PETR4")
