"""B3 taxonomy snapshot and the CVM single-level fallback (ADR 0024)."""

from smaug.portfolio.domain.sectors import portfolio_tickers
from smaug.portfolio.domain.taxonomy import (
    Classification,
    b3_classification,
    classify,
)


def test_snapshot_covers_every_portfolio_ticker_with_three_levels() -> None:
    for ticker in portfolio_tickers():
        c = b3_classification(ticker)
        assert c is not None, ticker
        assert c.setor, ticker
        assert c.subsetor, ticker
        assert c.segmento, ticker
        assert c.source == "b3"


def test_snapshot_classifies_a_unit_on_demand_ticker() -> None:
    c = b3_classification("KLBN11")
    assert c == Classification(
        "Materiais Básicos", "Madeira e Papel", "Papel e Celulose"
    )


def test_classify_prefers_the_snapshot_over_the_cvm_label() -> None:
    # A snapshot ticker keeps its full three levels even if a CVM label is given.
    assert classify("BBAS3", cvm_sector="Bancos") == b3_classification("BBAS3")


def test_classify_falls_back_to_the_cvm_single_level() -> None:
    c = classify("ABEV3", cvm_sector="Bebidas")  # not in the snapshot
    assert c == Classification("Bebidas", None, None)
    assert c is not None
    assert c.source == "cvm"


def test_classify_is_none_when_nothing_is_known() -> None:
    assert classify("NOPE99", cvm_sector=None) is None
    assert classify("NOPE99", cvm_sector="") is None
