"""Indicator calculator: annualization, sector awareness, growth (pure, no I/O)."""

from datetime import date
from decimal import Decimal

from smaug.analysis.domain.calculator import compute
from smaug.analysis.domain.financials import MarketData, StandardizedFinancials
from smaug.portfolio.domain.sectors import Sector

_Q3 = date(2024, 9, 30)  # YTD 9 months -> annualization factor 12/9


def _nonfinancial() -> StandardizedFinancials:
    return StandardizedFinancials(
        reference_date=_Q3,
        sector=Sector.COMMODITY,
        total_assets=Decimal(12000),
        equity=Decimal(6000),
        net_income=Decimal(900),  # annualized -> 1200
        revenue=Decimal(3000),
        gross_profit=Decimal(1500),
        ebitda=Decimal(1200),  # annualized -> 1600
        cash=Decimal(500),
        current_assets=Decimal(4000),
        current_liabilities=Decimal(2000),
        total_debt=Decimal(2000),
    )


def test_nonfinancial_computes_all_indicators() -> None:
    previous = StandardizedFinancials(
        reference_date=date(2023, 9, 30),
        sector=Sector.COMMODITY,
        revenue=Decimal(2400),
        net_income=Decimal(750),
    )
    market = MarketData(
        price=Decimal(12), market_cap=Decimal(12000), dividends_12m=Decimal(600)
    )

    ind = compute(_nonfinancial(), previous, market)

    assert ind.roe == Decimal("0.2")  # annualized 1200 / 6000
    assert ind.roa == Decimal("0.1")  # 1200 / 12000
    assert ind.net_margin == Decimal("0.3")  # 900 / 3000 (period ratio)
    assert ind.gross_margin == Decimal("0.5")
    assert ind.ebitda_margin == Decimal("0.4")
    assert ind.net_debt == Decimal(1500)  # 2000 - 500
    assert ind.net_debt_to_ebitda == Decimal("0.9375")  # 1500 / 1600
    assert ind.current_ratio == Decimal(2)
    assert ind.revenue_growth == Decimal("0.25")
    assert ind.net_income_growth == Decimal("0.2")
    assert ind.pe == Decimal(10)  # 12000 / 1200
    assert ind.pb == Decimal(2)  # 12000 / 6000
    assert ind.dividend_yield == Decimal("0.05")  # 600 / 12000
    assert ind.ev_ebitda == Decimal("8.4375")  # (12000 + 1500) / 1600


def test_bank_skips_inapplicable_indicators() -> None:
    bank = StandardizedFinancials(
        reference_date=_Q3,
        sector=Sector.BANK,
        equity=Decimal(8000),
        net_income=Decimal(600),  # annualized -> 800
        revenue=Decimal(3000),
        total_debt=Decimal(9999),  # present but must be ignored for a bank
        current_assets=Decimal(1),
        current_liabilities=Decimal(1),
    )
    ind = compute(bank, None, MarketData(market_cap=Decimal(8000)))

    assert ind.roe == Decimal("0.1")  # 800 / 8000
    assert ind.net_margin == Decimal("0.2")  # 600 / 3000
    assert ind.pe == Decimal(10)  # 8000 / 800
    assert ind.pb == Decimal(1)
    # Not applicable to a financial institution:
    assert ind.net_debt is None
    assert ind.net_debt_to_ebitda is None
    assert ind.current_ratio is None
    assert ind.ev_ebitda is None
    assert ind.ebitda_margin is None
    assert ind.gross_margin is None
    # No prior period -> no growth
    assert ind.revenue_growth is None
