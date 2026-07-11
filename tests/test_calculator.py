"""Indicator calculator: annualization, sector awareness, growth (pure, no I/O)."""

from dataclasses import replace
from datetime import date
from decimal import Decimal

from smaug.analysis.domain.calculator import compute
from smaug.analysis.domain.financials import (
    AccountingRegime,
    MarketData,
    StandardizedFinancials,
)
from smaug.analysis.domain.indicators import NullReason
from smaug.portfolio.domain.sectors import Sector

_Q3 = date(2024, 9, 30)  # YTD 9 months -> annualization factor 12/9


def _nonfinancial() -> StandardizedFinancials:
    return StandardizedFinancials(
        reference_date=_Q3,
        sector=Sector.COMMODITY,
        total_assets=Decimal(12000),
        equity=Decimal(6000),
        net_income=Decimal(900),  # annualized -> 1200
        revenue=Decimal(3000),  # annualized -> 4000
        gross_profit=Decimal(1500),
        ebit=Decimal(900),  # annualized -> 1200
        ebitda=Decimal(1200),  # annualized -> 1600
        cash=Decimal(500),
        current_assets=Decimal(4000),
        current_liabilities=Decimal(2000),
        total_debt=Decimal(2000),
        dividends_paid=Decimal(600),  # trailing payout, used for DY
        cfo=Decimal(1000),
        capex=Decimal(100),  # FCF period = 900 -> annualized 1200
    )


def test_nonfinancial_computes_all_indicators() -> None:
    previous = StandardizedFinancials(
        reference_date=date(2023, 9, 30),
        sector=Sector.COMMODITY,
        revenue=Decimal(2400),
        net_income=Decimal(750),
    )
    market = MarketData(
        price=Decimal(12), market_cap=Decimal(12000), shares=Decimal(600)
    )

    ind = compute(_nonfinancial(), previous, market)

    assert ind.roe == Decimal("0.2")  # annualized 1200 / 6000
    assert ind.roa == Decimal("0.1")  # 1200 / 12000
    assert ind.roic == Decimal("0.1056")  # 1200·(1-0.34) / (6000 + 1500)
    assert ind.net_margin == Decimal("0.3")  # 900 / 3000 (period ratio)
    assert ind.gross_margin == Decimal("0.5")
    assert ind.ebit_margin == Decimal("0.3")  # 900 / 3000 (period ratio)
    assert ind.ebitda_margin == Decimal("0.4")
    assert ind.asset_turnover == Decimal(4000) / Decimal(12000)  # annual rev / assets
    assert ind.eps == Decimal(2)  # annualized 1200 / 600 shares
    assert ind.bvps == Decimal(10)  # 6000 / 600 shares
    assert ind.net_debt == Decimal(1500)  # 2000 - 500
    assert ind.net_debt_to_ebitda == Decimal("0.9375")  # 1500 / 1600
    assert ind.debt_to_equity == Decimal(2000) / Decimal(6000)  # gross debt / equity
    assert ind.liabilities_to_assets == Decimal("0.5")  # (12000 - 6000) / 12000
    assert ind.current_ratio == Decimal(2)
    assert ind.revenue_growth == Decimal("0.25")
    assert ind.net_income_growth == Decimal("0.2")
    assert ind.pe == Decimal(10)  # 12000 / 1200
    assert ind.pb == Decimal(2)  # 12000 / 6000
    assert ind.psr == Decimal(3)  # 12000 / 4000 annual revenue
    assert ind.price_to_assets == Decimal(1)  # 12000 / 12000
    assert ind.price_to_ebit == Decimal(10)  # 12000 / 1200 annual EBIT
    assert ind.price_to_working_capital == Decimal(6)  # 12000 / (4000 - 2000)
    assert ind.payout == Decimal(600) / Decimal(900)  # dividends / net income
    assert ind.dividend_yield == Decimal("0.05")  # 600 / 12000
    assert ind.ev_ebitda == Decimal("8.4375")  # (12000 + 1500) / 1600
    assert ind.fcf == Decimal(1200)  # annualized (1000 - 100)
    assert ind.price_to_fcf == Decimal(10)  # 12000 / 1200
    assert ind.fcf_yield == Decimal("0.1")  # 1200 / 12000
    # Headline financials passed through unchanged (the period's own figure).
    assert ind.revenue == Decimal(3000)
    assert ind.net_income == Decimal(900)
    assert ind.dividends == Decimal(600)


def test_closed_year_leaves_annualization_a_no_op() -> None:
    # A December reference date is a full 12-month period, so annualizing (×12/12)
    # must leave the flows untouched — this is what makes the DFP closed-year view
    # correct without any calculator change.
    closed = StandardizedFinancials(
        reference_date=date(2024, 12, 31),
        sector=Sector.COMMODITY,
        equity=Decimal(6000),
        net_income=Decimal(1200),  # already annual: must NOT be scaled up
        revenue=Decimal(4000),
    )

    ind = compute(closed, None, MarketData(market_cap=Decimal(12000)))

    assert ind.roe == Decimal("0.2")  # 1200 / 6000, no 12/12 inflation
    assert ind.net_margin == Decimal("0.3")  # 1200 / 4000
    assert ind.pe == Decimal(10)  # 12000 / 1200


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
    assert ind.roic is None
    assert ind.ebit_margin is None
    assert ind.debt_to_equity is None
    assert ind.price_to_ebit is None
    assert ind.price_to_working_capital is None
    assert ind.fcf is None
    assert ind.price_to_fcf is None
    # No prior period -> no growth
    assert ind.revenue_growth is None


# The fields the CVM mapper deliberately skips for a financial-regime filer —
# mirrors mongo_fundamentals._FINANCIAL_UNMAPPED_FIELDS, inlined here so the
# domain test stays free of infrastructure imports.
_FINANCIAL_UNMAPPED = frozenset(
    {
        "gross_profit",
        "ebit",
        "ebitda",
        "dep_amort",
        "cash",
        "current_assets",
        "current_liabilities",
        "total_debt",
        "cfo",
        "capex",
    }
)


def test_bank_null_reasons_name_each_cause() -> None:
    bank = StandardizedFinancials(
        reference_date=_Q3,
        sector=Sector.BANK,
        total_assets=Decimal(90000),
        equity=Decimal(8000),
        net_income=Decimal(600),
        revenue=Decimal(3000),
        filed_regime=AccountingRegime.BANK,  # files as its sector predicts
        unmapped_fields=_FINANCIAL_UNMAPPED,
    )
    ind = compute(bank, None, MarketData(market_cap=Decimal(8000)))  # no shares

    # Cause 1 — genuinely meaningless for a bank (ADR 0010): it reports Basileia,
    # not net debt / EV-EBITDA, and has no EBITDA.
    assert ind.null_reasons["net_debt"] is NullReason.INAPPLICABLE_REGIME
    assert ind.null_reasons["net_debt_to_ebitda"] is NullReason.INAPPLICABLE_REGIME
    assert ind.null_reasons["debt_to_equity"] is NullReason.INAPPLICABLE_REGIME
    assert ind.null_reasons["ev_ebitda"] is NullReason.INAPPLICABLE_REGIME
    assert ind.null_reasons["ebitda_margin"] is NullReason.INAPPLICABLE_REGIME
    # Cause 2 — computable for a bank (the platforms show it), only unmapped by
    # us pending #48: margins, ROIC, EBIT/working-capital multiples, current ratio.
    assert ind.null_reasons["gross_margin"] is NullReason.SOURCE_ACCOUNT_UNMAPPED
    assert ind.null_reasons["ebit_margin"] is NullReason.SOURCE_ACCOUNT_UNMAPPED
    assert ind.null_reasons["roic"] is NullReason.SOURCE_ACCOUNT_UNMAPPED
    assert ind.null_reasons["current_ratio"] is NullReason.SOURCE_ACCOUNT_UNMAPPED
    assert ind.null_reasons["price_to_ebit"] is NullReason.SOURCE_ACCOUNT_UNMAPPED
    assert (
        ind.null_reasons["price_to_working_capital"]
        is NullReason.SOURCE_ACCOUNT_UNMAPPED
    )
    assert ind.null_reasons["fcf"] is NullReason.SOURCE_ACCOUNT_UNMAPPED
    assert ind.null_reasons["price_to_fcf"] is NullReason.SOURCE_ACCOUNT_UNMAPPED
    # Cause 3 — upstream inputs, each named individually:
    assert ind.null_reasons["eps"] is NullReason.MISSING_SHARE_COUNT
    assert ind.null_reasons["revenue_growth"] is NullReason.MISSING_PRIOR_PERIOD
    # The filing simply has no dividend line — absent, not unmapped:
    assert ind.null_reasons["payout"] is NullReason.SOURCE_ACCOUNT_ABSENT
    # Computed values never carry a reason:
    assert ind.roe is not None
    assert "roe" not in ind.null_reasons


def test_insurer_null_reasons_split_by_regime() -> None:
    # ADR 0010: an insurer is the near-mirror of a bank. Margins are degenerate
    # (both reference platforms show 0%), so they are inapplicable; net debt,
    # EV-EBITDA, debt/equity, ROIC and current ratio *are* shown by AUVP, so they
    # are merely unmapped pending #48 — not inapplicable.
    insurer = StandardizedFinancials(
        reference_date=_Q3,
        sector=Sector.INSURER,
        total_assets=Decimal(50000),
        equity=Decimal(9000),
        net_income=Decimal(2500),
        revenue=Decimal(4000),
        filed_regime=AccountingRegime.INSURANCE,  # files as its sector predicts
        unmapped_fields=_FINANCIAL_UNMAPPED,
    )
    ind = compute(insurer, None, MarketData(market_cap=Decimal(30000)))

    # Inapplicable for an insurer — the margins:
    assert ind.null_reasons["gross_margin"] is NullReason.INAPPLICABLE_REGIME
    assert ind.null_reasons["ebit_margin"] is NullReason.INAPPLICABLE_REGIME
    assert ind.null_reasons["ebitda_margin"] is NullReason.INAPPLICABLE_REGIME
    # Merely unmapped for an insurer — the leverage / valuation family:
    assert ind.null_reasons["net_debt"] is NullReason.SOURCE_ACCOUNT_UNMAPPED
    assert ind.null_reasons["net_debt_to_ebitda"] is NullReason.SOURCE_ACCOUNT_UNMAPPED
    assert ind.null_reasons["debt_to_equity"] is NullReason.SOURCE_ACCOUNT_UNMAPPED
    assert ind.null_reasons["ev_ebitda"] is NullReason.SOURCE_ACCOUNT_UNMAPPED
    assert ind.null_reasons["roic"] is NullReason.SOURCE_ACCOUNT_UNMAPPED
    assert ind.null_reasons["current_ratio"] is NullReason.SOURCE_ACCOUNT_UNMAPPED


def test_mismatched_filer_gets_unexpected_regime_not_inapplicable() -> None:
    # The CXSE3 case (ADR 0006): an insurer by sector that files as a holding.
    # Its regime-driven nulls are neither inapplicable nor a mapping gap — the
    # filer reports under a schema its sector does not predict.
    holding = StandardizedFinancials(
        reference_date=date(2024, 12, 31),
        sector=Sector.INSURER,
        total_assets=Decimal(20000),
        equity=Decimal(9000),
        net_income=Decimal(2500),
        revenue=Decimal(4000),
        filed_regime=AccountingRegime.CORPORATE,
        unmapped_fields=_FINANCIAL_UNMAPPED,
    )
    ind = compute(
        holding, None, MarketData(market_cap=Decimal(30000), shares=Decimal(3000))
    )

    assert ind.null_reasons["gross_margin"] is NullReason.UNEXPECTED_REGIME  # guarded
    assert ind.null_reasons["fcf"] is NullReason.UNEXPECTED_REGIME  # unmapped input
    assert ind.roe is not None  # the mapped core still computes


def test_missing_price_nulls_the_market_multiples_with_a_named_cause() -> None:
    # The #42 shape: fundamentals fine, no price -> every cap-based multiple
    # must say "missing price", not go silently null.
    ind = compute(_nonfinancial(), None, MarketData(shares=Decimal(600)))

    assert ind.pe is None
    assert ind.null_reasons["pe"] is NullReason.MISSING_PRICE
    assert ind.null_reasons["dividend_yield"] is NullReason.MISSING_PRICE
    assert ind.eps is not None  # per-share needs only the share count


def test_missing_shares_blames_the_share_count_not_the_price() -> None:
    # Closed-year cap = price × shares (ADR 0012): with the year's price present
    # but no filed share count, the cap-based multiples blame the shares.
    ind = compute(_nonfinancial(), None, MarketData(price=Decimal(6)))

    assert ind.pe is None
    assert ind.null_reasons["pe"] is NullReason.MISSING_SHARE_COUNT
    assert ind.null_reasons["pb"] is NullReason.MISSING_SHARE_COUNT


def test_zero_denominator_null_is_named() -> None:
    # A zero denominator is a known status, not an unclassified null: with every
    # input present, payout = dividends / 0 is the ZERO_DENOMINATOR dead-end (ANL-23).
    zero_income = replace(_nonfinancial(), net_income=Decimal(0))
    ind = compute(zero_income, None, MarketData(market_cap=Decimal(12000)))

    assert ind.payout is None  # dividends / 0
    assert ind.null_reasons["payout"] is NullReason.ZERO_DENOMINATOR
    assert ind.null_reasons["pe"] is NullReason.ZERO_DENOMINATOR  # cap / 0
