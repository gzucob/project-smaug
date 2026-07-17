"""The ticker → market-data symbol override map (#64)."""

from smaug.portfolio.domain import market_symbols
from smaug.portfolio.domain.market_symbols import market_symbol


def test_market_symbol_defaults_to_the_ticker_itself() -> None:
    assert market_symbol("PETR4") == "PETR4"


def test_market_symbol_applies_a_configured_override() -> None:
    market_symbols.TICKER_SYMBOL_OVERRIDES["OLDX3"] = "NEWX3"
    try:
        assert market_symbol("OLDX3") == "NEWX3"
    finally:
        del market_symbols.TICKER_SYMBOL_OVERRIDES["OLDX3"]
