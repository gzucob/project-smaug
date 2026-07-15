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


# The one line the CVM mapper still skips for a financial-regime filer — mirrors
# mongo_fundamentals._FINANCIAL_UNMAPPED_FIELDS, inlined here so the domain test
# stays free of infrastructure imports.
_FINANCIAL_UNMAPPED = frozenset({"dep_amort", "ebitda"})


def _mapped_bank() -> StandardizedFinancials:
    """A bank as the CVM mapper actually builds it (ADR 0015).

    It carries what a bank's chart of accounts holds — including 3.03 (net
    interest income, standing in for gross profit) and 3.05 (pre-tax profit,
    standing in for EBIT) — and *not* what it lacks: a bank files no debt line and
    no current/non-current split, so those stay ``None`` at the source rather than
    being blanked downstream.
    """
    return StandardizedFinancials(
        reference_date=_Q3,
        sector=Sector.BANK,
        total_assets=Decimal(90000),
        equity=Decimal(8000),
        net_income=Decimal(600),  # annualized -> 800
        revenue=Decimal(3000),
        gross_profit=Decimal(1200),  # 3.03 — net interest income
        ebit=Decimal(900),  # 3.05 — pre-tax result
        cash=Decimal(5000),
        cfo=Decimal(450),  # annualized -> 600
        capex=Decimal(150),  # annualized -> 200
        filed_regime=AccountingRegime.BANK,
        unmapped_fields=_FINANCIAL_UNMAPPED,
    )


def test_bank_computes_the_ratios_its_schema_supports() -> None:
    ind = compute(_mapped_bank(), None, MarketData(market_cap=Decimal(8000)))

    assert ind.roe == Decimal("0.1")  # 800 / 8000
    assert ind.net_margin == Decimal("0.2")  # 600 / 3000
    assert ind.pe == Decimal(10)  # 8000 / 800
    assert ind.pb == Decimal(1)
    # Mapped by #48 — these light up for a bank now, with no calculator guard:
    assert ind.gross_margin == Decimal("0.4")  # 1200 / 3000 — the spread
    assert ind.ebit_margin == Decimal("0.3")  # 900 / 3000
    assert ind.price_to_ebit == Decimal(8000) / Decimal(1200)  # ebit annualized
    assert ind.fcf == Decimal(400)  # (450 - 150), annualized
    assert ind.price_to_fcf == Decimal(20)  # 8000 / 400
    # Unbuildable from a bank's schema — no debt line, no current/non-current split:
    assert ind.net_debt is None
    assert ind.net_debt_to_ebitda is None
    assert ind.debt_to_equity is None
    assert ind.ev_ebitda is None
    assert ind.ebitda_margin is None
    assert ind.roic is None
    assert ind.current_ratio is None
    assert ind.price_to_working_capital is None
    # No prior period -> no growth
    assert ind.revenue_growth is None


def test_bank_computes_its_own_three_ratios() -> None:
    # ADR 0021, shaped on BBAS3's real filing. Its 3.03 spread (1200) is already net
    # of the loan-loss provision (-600), which the parent chart deducts inside the
    # intermediation expenses — so the margin the bank earned *before* writing
    # anything off is 1800, which is the *margem financeira bruta* it reports.
    bank = replace(
        _mapped_bank(),
        reference_date=date(2024, 12, 31),  # a closed year: no annualization
        total_assets=Decimal(60000),
        gross_profit=Decimal(1200),
        loan_loss_provision=Decimal(-600),
        fee_income=Decimal(400),
        personnel_expense=Decimal(-500),
        admin_expense=Decimal(-200),
        loan_book=Decimal(20000),
    )

    ind = compute(bank, None, MarketData(market_cap=Decimal(8000)))

    # spread before provisions = 1200 + 600 = 1800, over 60000 of assets
    assert ind.net_interest_margin == Decimal("0.03")
    # (500 + 200) of expense over (1800 + 400) of operating revenue — a cost, so it
    # reads positive even though CVM files both expenses negative
    assert ind.efficiency_ratio == Decimal(700) / Decimal(2200)
    assert ind.cost_of_risk == Decimal(600) / Decimal(20000)  # 600 written off / 20000


def test_the_bank_ratios_are_inapplicable_to_everyone_else() -> None:
    # A company that sells goods has no spread, no loan book and no payroll measured
    # against a spread. The null is a verdict of the regime, not a missing input.
    ind = compute(_nonfinancial(), None, MarketData(market_cap=Decimal(12000)))

    for name in ("net_interest_margin", "efficiency_ratio", "cost_of_risk"):
        assert getattr(ind, name) is None
        assert ind.null_reasons[name] is NullReason.INAPPLICABLE_REGIME


def test_bank_null_reasons_name_each_cause() -> None:
    ind = compute(
        _mapped_bank(), None, MarketData(market_cap=Decimal(8000))
    )  # no shares

    # Cause 1 — genuinely meaningless for a bank: it reports Basileia, not net debt
    # / EV-EBITDA, and has no EBITDA (ADR 0010). ADR 0015 adds the three the
    # mapping settled: a bank's balance sheet has no current/non-current split, so
    # its current ratio and P/working-capital are unbuildable, and its ROIC
    # denominator (equity + net debt) inherits the net-debt verdict.
    assert ind.null_reasons["net_debt"] is NullReason.INAPPLICABLE_REGIME
    assert ind.null_reasons["net_debt_to_ebitda"] is NullReason.INAPPLICABLE_REGIME
    assert ind.null_reasons["debt_to_equity"] is NullReason.INAPPLICABLE_REGIME
    assert ind.null_reasons["ev_ebitda"] is NullReason.INAPPLICABLE_REGIME
    assert ind.null_reasons["ebitda_margin"] is NullReason.INAPPLICABLE_REGIME
    assert ind.null_reasons["roic"] is NullReason.INAPPLICABLE_REGIME
    assert ind.null_reasons["current_ratio"] is NullReason.INAPPLICABLE_REGIME
    assert ind.null_reasons["price_to_working_capital"] is (
        NullReason.INAPPLICABLE_REGIME
    )
    # Cause 2 — nothing is merely *unmapped* for a bank any more: #48 mapped every
    # account whose absence used to be a mapping gap. This is the M1 win, so pin it.
    assert NullReason.SOURCE_ACCOUNT_UNMAPPED not in ind.null_reasons.values()
    # Cause 3 — upstream inputs, each named individually:
    assert ind.null_reasons["eps"] is NullReason.MISSING_SHARE_COUNT
    assert ind.null_reasons["revenue_growth"] is NullReason.MISSING_PRIOR_PERIOD
    # The filing simply has no dividend line — absent, not unmapped:
    assert ind.null_reasons["payout"] is NullReason.SOURCE_ACCOUNT_ABSENT
    # Computed values never carry a reason:
    assert ind.roe is not None
    assert "roe" not in ind.null_reasons


def test_insurer_null_reasons_split_by_regime() -> None:
    # ADR 0010: an insurer is the near-mirror of a bank — its margins are
    # degenerate (both reference platforms show 0%), so they are inapplicable.
    # ADR 0015: unlike a bank, it files a corporate-shaped balance sheet, so its
    # current ratio computes; but the insurer schema has no borrowings line at all
    # (2.01.04 is "Capitalização" there), so the leverage family is *absent* at the
    # source — we looked and there is nothing to read — rather than unmapped.
    insurer = StandardizedFinancials(
        reference_date=_Q3,
        sector=Sector.INSURER,
        total_assets=Decimal(50000),
        equity=Decimal(9000),
        net_income=Decimal(2500),
        revenue=Decimal(4000),
        ebit=Decimal(3000),  # 3.07 for an insurer, not 3.05
        current_assets=Decimal(6000),
        current_liabilities=Decimal(3000),
        filed_regime=AccountingRegime.INSURANCE,  # files as its sector predicts
        unmapped_fields=_FINANCIAL_UNMAPPED,
    )
    ind = compute(insurer, None, MarketData(market_cap=Decimal(30000)))

    # Inapplicable for an insurer — the margins:
    assert ind.null_reasons["gross_margin"] is NullReason.INAPPLICABLE_REGIME
    assert ind.null_reasons["ebit_margin"] is NullReason.INAPPLICABLE_REGIME
    assert ind.null_reasons["ebitda_margin"] is NullReason.INAPPLICABLE_REGIME
    # Absent at the source — the insurer schema files no debt line:
    assert ind.null_reasons["net_debt"] is NullReason.SOURCE_ACCOUNT_ABSENT
    assert ind.null_reasons["net_debt_to_ebitda"] is NullReason.SOURCE_ACCOUNT_ABSENT
    assert ind.null_reasons["debt_to_equity"] is NullReason.SOURCE_ACCOUNT_ABSENT
    assert ind.null_reasons["ev_ebitda"] is NullReason.SOURCE_ACCOUNT_ABSENT
    assert ind.null_reasons["roic"] is NullReason.SOURCE_ACCOUNT_ABSENT
    # Its balance sheet *does* carry the current/non-current split a bank lacks:
    assert ind.current_ratio == Decimal(2)  # 6000 / 3000
    assert "current_ratio" not in ind.null_reasons


def test_applicability_follows_the_filed_regime_not_the_sector() -> None:
    # The CXSE3 case (ADR 0006/0020): an insurer by sector that files as a holding.
    # Applicability is a property of the chart of accounts the company *uses*, so
    # this filer is judged as the corporate it files as — the insurer's suppressed
    # margins are not applied to it, and a null it does have gets a filed cause.
    holding = StandardizedFinancials(
        reference_date=date(2024, 12, 31),
        sector=Sector.INSURER,
        total_assets=Decimal(20000),
        equity=Decimal(9000),
        net_income=Decimal(2500),
        revenue=Decimal(4000),
        filed_regime=AccountingRegime.CORPORATE,
        # Filing corporately, it is now *mapped* corporately (ADR 0015), so nothing
        # is deliberately skipped for it — its unmapped set is empty.
        unmapped_fields=frozenset(),
    )
    ind = compute(
        holding, None, MarketData(market_cap=Decimal(30000), shares=Decimal(3000))
    )

    # Not suppressed: the corporate schema it files under supports a gross margin,
    # and this filing simply carries no gross-profit line. That is an absence in the
    # filing, not a verdict of ours — which is the whole difference (#95).
    assert ind.null_reasons["gross_margin"] is NullReason.SOURCE_ACCOUNT_ABSENT
    assert ind.null_reasons["fcf"] is NullReason.SOURCE_ACCOUNT_ABSENT
    assert ind.roe is not None  # the mapped core still computes


def test_a_filer_is_judged_by_the_regime_it_files_even_when_its_sector_agrees() -> None:
    # The other side of #95: a bank that files as one keeps the bank's inapplicable
    # set, and its null says so — the change is *which* regime is asked, not whether
    # a regime decides.
    bank = StandardizedFinancials(
        reference_date=date(2024, 12, 31),
        sector=Sector.BANK,
        total_assets=Decimal(50000),
        equity=Decimal(8000),
        net_income=Decimal(900),
        revenue=Decimal(4000),
        filed_regime=AccountingRegime.BANK,
    )

    ind = compute(bank, None, MarketData(market_cap=Decimal(30000)))

    assert ind.net_debt is None
    assert ind.null_reasons["net_debt"] is NullReason.INAPPLICABLE_REGIME


def test_scale_figures_are_carried_through_to_the_output() -> None:
    # #25: market cap, enterprise value and the share count are the calculator's own
    # market-side inputs — persisted, not discarded, so the front-end can show them.
    market = MarketData(
        price=Decimal(12), market_cap=Decimal(12000), shares=Decimal(600)
    )

    ind = compute(_nonfinancial(), None, market)

    assert ind.market_cap == Decimal(12000)
    assert ind.shares == Decimal(600)
    # EV = cap + net debt (total_debt 2000 − cash 500 = 1500).
    assert ind.enterprise_value == Decimal(13500)


def test_enterprise_value_is_inapplicable_for_a_bank() -> None:
    # EV inherits net debt's applicability: a bank has no borrowings line, so both are
    # inapplicable under the regime — named, not silently missing (like net_debt).
    ind = compute(_mapped_bank(), None, MarketData(market_cap=Decimal(8000)))

    assert ind.market_cap == Decimal(8000)  # the cap itself is still meaningful
    assert ind.enterprise_value is None
    assert ind.null_reasons["enterprise_value"] is NullReason.INAPPLICABLE_REGIME


def test_missing_price_nulls_the_market_multiples_with_a_named_cause() -> None:
    # The #42 shape: fundamentals fine, no price -> every cap-based multiple
    # must say "missing price", not go silently null.
    ind = compute(_nonfinancial(), None, MarketData(shares=Decimal(600)))

    assert ind.pe is None
    assert ind.null_reasons["pe"] is NullReason.MISSING_PRICE
    assert ind.null_reasons["dividend_yield"] is NullReason.MISSING_PRICE
    assert ind.eps is not None  # per-share needs only the share count


def test_missing_shares_blames_the_share_count_not_the_price() -> None:
    # The cap sums the company's share classes (ADR 0014), so the use case is the
    # one that knows which input it was missing and hands the reason over. Here
    # the year's price is present and the filed count is not.
    ind = compute(
        _nonfinancial(),
        None,
        MarketData(price=Decimal(6), cap_null_reason=NullReason.MISSING_SHARE_COUNT),
    )

    assert ind.pe is None
    assert ind.null_reasons["pe"] is NullReason.MISSING_SHARE_COUNT
    assert ind.null_reasons["pb"] is NullReason.MISSING_SHARE_COUNT


def test_a_sibling_class_without_a_quote_blames_the_price() -> None:
    # A dual-class company whose ON class has no quote cannot be capitalized even
    # though the analyzed ticker's own price and share count are both in hand —
    # the null cap blames the missing price, not the shares it does have.
    ind = compute(
        _nonfinancial(),
        None,
        MarketData(
            price=Decimal(6),
            shares=Decimal(600),
            cap_null_reason=NullReason.MISSING_PRICE,
        ),
    )

    assert ind.pe is None
    assert ind.null_reasons["pe"] is NullReason.MISSING_PRICE
    assert ind.eps is not None  # the per-share side still has its count


def test_zero_denominator_null_is_named() -> None:
    # A zero denominator is a known status, not an unclassified null: with every
    # input present, payout = dividends / 0 is the ZERO_DENOMINATOR dead-end (ANL-23).
    zero_income = replace(_nonfinancial(), net_income=Decimal(0))
    ind = compute(zero_income, None, MarketData(market_cap=Decimal(12000)))

    assert ind.payout is None  # dividends / 0
    assert ind.null_reasons["payout"] is NullReason.ZERO_DENOMINATOR
    assert ind.null_reasons["pe"] is NullReason.ZERO_DENOMINATOR  # cap / 0
