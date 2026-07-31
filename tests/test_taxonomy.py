"""B3 taxonomy snapshot and the CVM single-level fallback (ADR 0024)."""

from smaug.portfolio.domain.sectors import portfolio_tickers
from smaug.portfolio.domain.taxonomy import (
    Classification,
    b3_classification,
    classify,
    snapshot_tickers,
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
    c = classify("XXXX3", cvm_sector="Bebidas")  # a ticker never in the snapshot
    assert c == Classification("Bebidas", None, None)
    assert c is not None
    assert c.source == "cvm"


def test_classify_is_none_when_nothing_is_known() -> None:
    assert classify("NOPE99", cvm_sector=None) is None
    assert classify("NOPE99", cvm_sector="") is None


# The values a human read off B3's public tool, before the snapshot was
# generated. They are the check on the generator: a regenerated snapshot that
# disagrees with any of them means the pipeline — the endpoint, the level split,
# or a label correction — has drifted from what B3 actually publishes.
_HAND_VERIFIED: dict[str, Classification] = {
    "PETR4": Classification(
        "Petróleo, Gás e Biocombustíveis",
        "Petróleo, Gás e Biocombustíveis",
        "Exploração, Refino e Distribuição",
    ),
    "VALE3": Classification("Materiais Básicos", "Mineração", "Minerais Metálicos"),
    "SAPR11": Classification(
        "Utilidade Pública", "Água e Saneamento", "Água e Saneamento"
    ),
    "TAEE11": Classification(
        "Utilidade Pública", "Energia Elétrica", "Energia Elétrica"
    ),
    "WEGE3": Classification(
        "Bens Industriais", "Máquinas e Equipamentos", "Motores, Compressores e Outros"
    ),
    "BBAS3": Classification("Financeiro", "Intermediários Financeiros", "Bancos"),
    "BBDC4": Classification("Financeiro", "Intermediários Financeiros", "Bancos"),
    "BBSE3": Classification("Financeiro", "Previdência e Seguros", "Seguradoras"),
    "CXSE3": Classification("Financeiro", "Previdência e Seguros", "Seguradoras"),
    "KLBN11": Classification(
        "Materiais Básicos", "Madeira e Papel", "Papel e Celulose"
    ),
    "ABEV3": Classification(
        "Consumo não Cíclico", "Bebidas", "Cervejas e Refrigerantes"
    ),
    "LREN3": Classification(
        "Consumo Cíclico", "Comércio", "Tecidos, Vestuário e Calçados"
    ),
    "HAPV3": Classification(
        "Saúde",
        "Serviços Médico-Hospitalares, Análises e Diagnósticos",
        "Serviços Médico-Hospitalares, Análises e Diagnósticos",
    ),
    "TOTS3": Classification(
        "Tecnologia da Informação", "Programas e Serviços", "Programas e Serviços"
    ),
    "VIVT3": Classification("Comunicações", "Telecomunicações", "Telecomunicações"),
}


def test_the_generated_snapshot_still_agrees_with_every_hand_verified_entry() -> None:
    for ticker, expected in _HAND_VERIFIED.items():
        assert b3_classification(ticker) == expected, ticker


def test_the_snapshot_covers_the_exchange_not_a_handful() -> None:
    # It replaced 15 hand-typed entries; a regeneration that collapses back to a
    # handful is a broken fetch, not a smaller exchange.
    assert len(snapshot_tickers()) > 400


def test_no_label_carries_the_endpoints_comma_mangling() -> None:
    """B3's endpoint writes "Petróleo. Gás"; the snapshot must not."""
    for ticker in snapshot_tickers():
        c = b3_classification(ticker)
        assert c is not None
        for level in (c.setor, c.subsetor, c.segmento):
            assert level is not None
            assert ". " not in level, (ticker, level)
