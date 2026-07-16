"""B3 economic classification — setor → subsetor → segmento.

The B3 groups every listed company in a three-level economic taxonomy. That
taxonomy is a **B3 artifact**, published by the exchange and refreshed weekly —
it is *not* in CVM open data, whose registry carries only a single
``Setor_Atividade`` label (the FCA/cad, see ADR 0023). So the three levels come
from a **committed snapshot** here (reference data, like ``cvm_codes.py``), and a
ticker outside the snapshot degrades gracefully to the CVM single level
(``subsetor``/``segmento`` unknown) — never an error, never a blank screen.

The snapshot is hand-verified against B3's *Classificação Setorial*. Keeping it
current for the whole exchange (and regenerating it from B3's file) is a
follow-up; today it covers the analysed set, and the CVM fallback covers the
rest.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Classification:
    """A company's economic classification.

    ``setor`` is always present — the B3 *setor econômico* when the ticker is in
    the snapshot, otherwise the CVM ``Setor_Atividade`` as a single-level
    fallback. ``subsetor``/``segmento`` are ``None`` under that fallback: unknown,
    not inapplicable.
    """

    setor: str
    subsetor: str | None = None
    segmento: str | None = None

    @property
    def source(self) -> str:
        """Where the classification came from: full B3 vs the CVM fallback."""
        return "b3" if self.subsetor is not None else "cvm"


# Committed snapshot of B3's Classificação Setorial (setor, subsetor, segmento),
# keyed by trading ticker. Covers the nine portfolio tickers plus the tickers
# analysed on demand so far. Verified against B3's public tool.
B3_TAXONOMY: dict[str, Classification] = {
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
    # Sector representatives — one liquid name per B3 setor econômico not covered
    # by the nine, ingested to broaden the fidelity comparison across sectors.
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
    "VIVT3": Classification(
        "Comunicações", "Telecomunicações", "Telecomunicações"
    ),
}


def b3_classification(ticker: str) -> Classification | None:
    """The full B3 three-level classification for ``ticker``, if in the snapshot."""
    return B3_TAXONOMY.get(ticker.upper().strip())


def classify(ticker: str, cvm_sector: str | None) -> Classification | None:
    """Resolve a ticker's classification: B3 snapshot, else the CVM fallback.

    Returns ``None`` only when neither is available (an unknown ticker) — the
    caller turns that into ``UnknownTickerError``. When only ``cvm_sector`` is
    known, ``subsetor``/``segmento`` stay ``None`` (single-level fallback).
    """
    snapshot = b3_classification(ticker)
    if snapshot is not None:
        return snapshot
    if cvm_sector:
        return Classification(cvm_sector)
    return None
