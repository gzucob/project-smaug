"""Deterministic ticker -> CVM registrant code (fixed reference data).

CVM open data is keyed by ``CD_CVM`` (the registrant code), never by the B3
trading ticker — tickers simply do not exist in CVM datasets. So this de/para
is curated and must be kept in sync with the portfolio. Codes below were
verified against the company names in the real 2024 ITR file (``DENOM_CIA``).
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


def cvm_code_of(ticker: str) -> str:
    """Return the CVM code for ``ticker``, raising ``KeyError`` if unmapped."""
    return TICKER_TO_CVM_CODE[ticker]
