"""B3 taxonomy snapshot and the CVM single-level fallback (ADR 0024)."""

from smaug.portfolio.domain.taxonomy import (
    Classification,
    b3_classification,
    classify,
    snapshot_tickers,
)


def test_snapshot_covers_every_official_ticker_with_three_levels() -> None:
    for ticker in _OFFICIAL:
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


# What B3's official *Classificação Setorial* spreadsheet publishes for the
# fifteen tickers that used to be the whole snapshot. They are the check on the
# generator: a regenerated snapshot disagreeing with any of them means the
# pipeline — the spreadsheet parse, the root join, or the fallback's label
# corrections — has drifted from what B3 says.
#
# Three of them read oddly, and that is B3's own text, not ours. Its **web tool**
# renders "Serviços Médico-Hospitalares, Análises e Diagnósticos" and
# "Motores, Compressores e Outros"; its **published spreadsheet** abbreviates the
# first and keeps a space before the comma in the second. The two surfaces
# disagree, and the spreadsheet is the one B3 publishes as the classification.
_OFFICIAL: dict[str, Classification] = {
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
        "Bens Industriais",
        "Máquinas e Equipamentos",
        "Motores , Compressores e Outros",  # the space is B3's
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
        "Consumo Cíclico",
        "Comércio Varejista",  # B3 renamed the old "Comércio"
        "Tecidos, Vestuário e Calçados",
    ),
    "HAPV3": Classification(
        "Saúde",
        "Serv.Méd.Hospit.,Análises e Diagnósticos",  # B3 abbreviates it
        "Serv.Méd.Hospit.,Análises e Diagnósticos",
    ),
    "TOTS3": Classification(
        "Tecnologia da Informação", "Programas e Serviços", "Programas e Serviços"
    ),
    "VIVT3": Classification("Comunicações", "Telecomunicações", "Telecomunicações"),
}


def test_the_generated_snapshot_matches_b3s_published_spreadsheet() -> None:
    for ticker, expected in _OFFICIAL.items():
        assert b3_classification(ticker) == expected, ticker


def test_the_snapshot_covers_the_exchange_not_a_handful() -> None:
    # It replaced 15 hand-typed entries; a regeneration that collapses back to a
    # handful is a broken fetch, not a smaller exchange.
    assert len(snapshot_tickers()) > 400


def test_no_label_carries_the_endpoints_comma_mangling() -> None:
    """The per-company endpoint writes "Petróleo. Gás"; the snapshot must not.

    Checked against the vocabulary B3's spreadsheet actually publishes rather
    than by hunting for periods — some of its labels legitimately contain one
    ("Máq. e Equip. Industriais"), and a rule that flagged those would have to be
    taught the difference, which is the mistake this replaced.
    """
    mangled = {
        "Petróleo. Gás e Biocombustíveis",
        "Exploração. Refino e Distribuição",
        "Tecidos. Vestuário e Calçados",
        "Motores . Compressores e Outros",
        "Serv.Méd.Hospit..Análises e Diagnósticos",
        "Comércio",
    }
    for ticker in snapshot_tickers():
        c = b3_classification(ticker)
        assert c is not None
        assert not ({c.setor, c.subsetor, c.segmento} & mangled), ticker
