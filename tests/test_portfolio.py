"""Portfolio reference data: the sector map and the listed share classes."""

import pytest

from smaug.portfolio.domain.sectors import (
    Sector,
    portfolio_tickers,
    require_portfolio_tickers,
    sector_of,
)
from smaug.portfolio.domain.share_classes import (
    ShareKind,
    is_unit,
    listed_classes,
)
from smaug.shared.errors import UnknownTickerError


def test_should_list_nine_tickers_when_portfolio_read() -> None:
    assert len(portfolio_tickers()) == 9


def test_should_flag_bank_as_financial_when_ticker_is_bbas3() -> None:
    assert sector_of("BBAS3") is Sector.BANK
    assert sector_of("BBAS3").is_financial


def test_should_flag_commodity_as_non_financial_when_ticker_is_petr4() -> None:
    assert sector_of("PETR4") is Sector.COMMODITY
    assert not sector_of("PETR4").is_financial


def test_should_raise_unknown_ticker_error_when_ticker_unknown() -> None:
    with pytest.raises(UnknownTickerError, match="NOPE3"):
        sector_of("NOPE3")


def test_should_pass_require_portfolio_tickers_when_all_known() -> None:
    require_portfolio_tickers(portfolio_tickers())  # no raise


def test_should_raise_require_portfolio_tickers_when_one_is_unknown() -> None:
    # A typo mixed with real tickers is a user error, caught up front (#60).
    with pytest.raises(UnknownTickerError, match="ZZZZ99"):
        require_portfolio_tickers(["PETR4", "ZZZZ99"])


def test_every_portfolio_ticker_has_its_listed_classes() -> None:
    # A ticker with no composition cannot be capitalized (ADR 0014), so the map
    # must not fall behind the portfolio.
    for ticker in portfolio_tickers():
        assert listed_classes(ticker), ticker


def test_a_dual_class_ticker_lists_its_sibling() -> None:
    # Petrobras is worth PETR3 + PETR4, not PETR4 alone.
    assert [c.symbol for c in listed_classes("PETR4")] == ["PETR3", "PETR4"]
    assert [c.kind for c in listed_classes("PETR4")] == [
        ShareKind.COMMON,
        ShareKind.PREFERRED,
    ]


def test_a_unit_lists_the_classes_underneath_it_not_itself() -> None:
    # The bundle has no share count of its own; the classes under it do.
    assert [c.symbol for c in listed_classes("SAPR11")] == ["SAPR3", "SAPR4"]
    assert is_unit("SAPR11")


def test_a_single_class_ticker_lists_only_itself() -> None:
    assert [c.symbol for c in listed_classes("WEGE3")] == ["WEGE3"]
    assert not is_unit("WEGE3")


def test_an_unknown_ticker_has_no_listed_classes() -> None:
    assert listed_classes("ZZZZ99") == ()
