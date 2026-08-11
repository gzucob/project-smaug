"""The listed universe: what counts as a trading code, and how codes group.

The rejected strings here are not invented — they are the literal contents of
``Codigo_Negociacao`` in the real 2024 FCA archive, where 41 of 547 rows hold
something that is not a ticker. Pinning them is the point: a batch that walks the
index end to end is the first caller that ever sees them.
"""

from __future__ import annotations

from datetime import date

import pytest

from smaug.portfolio.domain.company import CompanyIdentity, InstrumentKind
from smaug.portfolio.domain.universe import (
    ListedCompany,
    is_trading_code,
    listed_companies,
)


def _identity(
    ticker: str,
    cd_cvm: str,
    *,
    cnpj: str = "00.000.000/0001-00",
    kind: InstrumentKind = InstrumentKind.COMMON_SHARE,
    trading_ended: date | None = None,
) -> CompanyIdentity:
    return CompanyIdentity(
        ticker=ticker,
        cd_cvm=cd_cvm,
        cnpj=cnpj,
        denom="COMPANHIA TESTE S.A.",
        cvm_sector="Teste",
        situation="Ativo",
        instrument_kind=kind,
        instrument_type=kind.value,
        trading_ended=trading_ended,
    )


@pytest.mark.parametrize(
    "code",
    ["PETR4", "VALE3", "SAPR11", "B3SA3", "INBR32", "BRGE7", "BHIA12", "CELP7"],
)
def test_accepts_every_shape_b3_actually_lists(code: str) -> None:
    assert is_trading_code(code)


@pytest.mark.parametrize(
    "junk",
    [
        "0",  # a bare digit
        "00000",
        "022055",  # a registration number in the trading-code column
        "1545-8",
        "468-5",
        "N/A",
        "NAO HA",
        "ADR",
        "B3",  # too short to carry a class number
        "BRQB",  # a root with no class number
        "EQMA3B",  # organized-OTC code, not a B3 trading code
        "SPRT3B",
    ],
)
def test_rejects_what_the_fca_column_actually_collects(junk: str) -> None:
    assert not is_trading_code(junk)


def test_a_registrants_classes_collapse_into_one_company() -> None:
    companies = listed_companies(
        [
            _identity("ELET6", "2437"),
            _identity("ELET3", "2437"),
            _identity("ELET5", "2437"),
        ]
    )

    assert companies == (
        ListedCompany(
            cd_cvm="2437",
            cnpj="00.000.000/0001-00",
            denom="COMPANHIA TESTE S.A.",
            ticker="ELET3",
            tickers=("ELET3", "ELET5", "ELET6"),
        ),
    )


def test_the_primary_ticker_is_the_ordinary_share() -> None:
    """ON names the company. Without one, the lowest class number does."""
    on_and_pn = listed_companies(
        [_identity("PETR4", "9512"), _identity("PETR3", "9512")]
    )
    assert on_and_pn[0].ticker == "PETR3"

    unit_and_pn = listed_companies(
        [
            _identity("SAPR11", "18627", kind=InstrumentKind.UNIT),
            _identity("SAPR4", "18627", kind=InstrumentKind.PREFERRED_SHARE),
        ]
    )
    assert unit_and_pn[0].ticker == "SAPR4"


def test_a_non_ticker_never_becomes_a_company() -> None:
    companies = listed_companies(
        [_identity("WEGE3", "5410"), _identity("N/A", "99999"), _identity("0", "88888")]
    )

    assert [c.cd_cvm for c in companies] == ["5410"]


def test_only_current_shares_and_units_enter_the_fundamental_universe() -> None:
    companies = listed_companies(
        [
            _identity("WEGE3", "5410"),
            _identity("SAPR11", "18627", kind=InstrumentKind.UNIT),
            _identity("BEEF11", "9991", kind=InstrumentKind.SUBSCRIPTION_WARRANT),
            _identity(
                "BMGB11",
                "9992",
                kind=InstrumentKind.UNIT,
                trading_ended=date(2019, 11, 28),
            ),
        ]
    )

    assert {company.ticker for company in companies} == {"WEGE3", "SAPR11"}


def test_the_batch_order_does_not_depend_on_the_index_order() -> None:
    """A resumed batch must cover the same ground in the same order."""
    forward = listed_companies([_identity("AAAA3", "300"), _identity("BBBB3", "12")])
    backward = listed_companies([_identity("BBBB3", "12"), _identity("AAAA3", "300")])

    assert forward == backward
    assert [c.cd_cvm for c in forward] == ["12", "300"]
