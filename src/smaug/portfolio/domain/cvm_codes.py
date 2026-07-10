"""Deterministic ticker -> CVM registrant keys (fixed reference data).

CVM open data is keyed by ``CD_CVM`` (the registrant code), never by the B3
trading ticker — tickers simply do not exist in CVM datasets. So this de/para
is curated and must be kept in sync with the portfolio. Codes below were
verified against the company names in the real 2024 ITR file (``DENOM_CIA``).

The DFP/ITR statements are keyed by ``CD_CVM``, but the FRE (which carries the
capital composition) is keyed by **CNPJ** instead — hence the second map. Both
were cross-checked against CVM's ``cad_cia_aberta.csv`` registry.
"""

from __future__ import annotations

# Ticker -> CVM code (string, no leading zeros — matches pycvm's ``cvm_code``).
TICKER_TO_CVM_CODE: dict[str, str] = {
    "PETR4": "9512",  # PETROLEO BRASILEIRO S.A. PETROBRAS
    "VALE3": "4170",  # VALE S.A.
    "SAPR11": "18627",  # CIA SANEAMENTO DO PARANA - SANEPAR
    "TAEE11": "20257",  # TRANSMISSORA ALIANCA DE ENERGIA ELETRICA S.A. (Taesa)
    "WEGE3": "5410",  # WEG S.A.
    "BBAS3": "1023",  # BCO BRASIL S.A.
    "BBDC4": "906",  # BCO BRADESCO S.A.
    "BBSE3": "23159",  # BB SEGURIDADE PARTICIPACOES S.A.
    "CXSE3": "23795",  # CAIXA SEGURIDADE PARTICIPACOES S.A.
}


# Ticker -> CNPJ, formatted exactly as the FRE CSVs write it (with punctuation).
TICKER_TO_CNPJ: dict[str, str] = {
    "PETR4": "33.000.167/0001-01",  # PETROLEO BRASILEIRO S.A. PETROBRAS
    "VALE3": "33.592.510/0001-54",  # VALE S.A.
    "SAPR11": "76.484.013/0001-45",  # CIA. DE SANEAMENTO DO PARANA - SANEPAR
    "TAEE11": "07.859.971/0001-30",  # TRANSMISSORA ALIANCA DE ENERGIA ELETRICA
    "WEGE3": "84.429.695/0001-11",  # WEG S.A.
    "BBAS3": "00.000.000/0001-91",  # BANCO DO BRASIL S.A.
    "BBDC4": "60.746.948/0001-12",  # BANCO BRADESCO S.A.
    "BBSE3": "17.344.597/0001-94",  # BB SEGURIDADE PARTICIPACOES S.A.
    "CXSE3": "22.543.331/0001-00",  # CAIXA SEGURIDADE PARTICIPACOES S.A.
}


def cvm_code_of(ticker: str) -> str:
    """Return the CVM code for ``ticker``, raising ``KeyError`` if unmapped."""
    return TICKER_TO_CVM_CODE[ticker]


def cnpj_of(ticker: str) -> str:
    """Return the CNPJ for ``ticker``, raising ``KeyError`` if unmapped."""
    return TICKER_TO_CNPJ[ticker]
