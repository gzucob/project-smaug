"""Portfolio sector map (reference data)."""

import pytest

from smaug.portfolio.domain.sectors import Sector, portfolio_tickers, sector_of


def test_should_list_nine_tickers_when_portfolio_read() -> None:
    assert len(portfolio_tickers()) == 9


def test_should_flag_bank_as_financial_when_ticker_is_bbas3() -> None:
    assert sector_of("BBAS3") is Sector.BANK
    assert sector_of("BBAS3").is_financial


def test_should_flag_commodity_as_non_financial_when_ticker_is_petr4() -> None:
    assert sector_of("PETR4") is Sector.COMMODITY
    assert not sector_of("PETR4").is_financial


def test_should_raise_key_error_when_ticker_unknown() -> None:
    with pytest.raises(KeyError):
        sector_of("NOPE3")
