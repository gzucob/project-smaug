"""TTM assembly: sum isolated quarter flows, latest stocks, derive the missing Q4."""

from dataclasses import replace
from datetime import date
from decimal import Decimal

from smaug.analysis.domain.financials import AccountingRegime, StandardizedFinancials
from smaug.analysis.domain.ttm import build_ttm
from smaug.portfolio.domain.sectors import Sector


def _q(
    end: date,
    *,
    revenue: Decimal | None = None,
    net_income: Decimal | None = None,
    equity: Decimal | None = None,
    period_start: date | None = None,
    dep_amort: Decimal | None = None,
    dividends_paid: Decimal | None = None,
    cfo: Decimal | None = None,
    capex: Decimal | None = None,
    dfc_period_start: date | None = None,
) -> StandardizedFinancials:
    return StandardizedFinancials(
        reference_date=end,
        sector=Sector.COMMODITY,
        period_start=period_start,
        dfc_period_start=dfc_period_start,
        revenue=revenue,
        net_income=net_income,
        equity=equity,
        dep_amort=dep_amort,
        dividends_paid=dividends_paid,
        cfo=cfo,
        capex=capex,
    )


_ENDS = (
    date(2025, 6, 30),
    date(2025, 9, 30),
    date(2025, 12, 31),
    date(2026, 3, 31),
)


def test_ttm_sums_isolated_flows_and_takes_latest_stocks() -> None:
    quarters = [_q(e, revenue=Decimal(1000), net_income=Decimal(100)) for e in _ENDS]
    quarters[-1] = _q(
        _ENDS[-1], revenue=Decimal(1000), net_income=Decimal(100), equity=Decimal(6000)
    )

    ttm = build_ttm(quarters, None)

    assert ttm is not None
    assert ttm.revenue == Decimal(4000)  # 4 × 1000
    assert ttm.net_income == Decimal(400)
    assert ttm.equity == Decimal(6000)  # stock: latest quarter, not summed
    assert ttm.reference_date == date(2026, 3, 31)
    assert ttm.period_start == date(2025, 4, 1)  # 12 months back → annualization no-op


def test_ttm_carries_the_null_cause_provenance() -> None:
    # filed_regime / unmapped_fields (#30) must survive the TTM assembly, or the
    # live view would lose the ability to attribute its nulls.
    quarters = [
        replace(
            _q(e, revenue=Decimal(1000)),
            filed_regime=AccountingRegime.BANK,
            unmapped_fields=frozenset({"cfo", "capex"}),
        )
        for e in _ENDS
    ]

    ttm = build_ttm(quarters, None)

    assert ttm is not None
    assert ttm.filed_regime is AccountingRegime.BANK
    assert ttm.unmapped_fields == frozenset({"cfo", "capex"})


def test_ttm_normalizes_ytd_quarters_and_derives_q4_from_annual() -> None:
    # ITR income statements filed year-to-date; Q4 has no ITR, only the annual DFP.
    jan = date(2025, 1, 1)
    quarters = [
        _q(date(2025, 3, 31), revenue=Decimal(100), period_start=jan),  # Q1 YTD
        _q(date(2025, 6, 30), revenue=Decimal(250), period_start=jan),  # 6-month YTD
        _q(date(2025, 9, 30), revenue=Decimal(390), period_start=jan),  # 9-month YTD
    ]
    annual = _q(date(2025, 12, 31), revenue=Decimal(500), equity=Decimal(8000))

    ttm = build_ttm(quarters, annual)

    assert ttm is not None
    # Isolated: 100, 150, 140, and Q4 = 500 − 390 = 110 → full year = 500.
    assert ttm.revenue == Decimal(500)
    assert ttm.reference_date == date(2025, 12, 31)  # window ends on the closed year
    assert ttm.equity == Decimal(8000)  # stock from the annual (latest balance)


def test_ttm_isolates_dfc_flows_on_their_own_year_to_date_span() -> None:
    # The DRE is isolated quarters but the DFC is year-to-date, so D&A and
    # dividends must isolate on the DFC span, independent of the DRE span.
    jan = date(2025, 1, 1)
    quarters = [
        _q(  # DRE Q1 isolated; DFC YTD 3m
            date(2025, 3, 31),
            revenue=Decimal(100),
            period_start=jan,
            dep_amort=Decimal(10),
            dividends_paid=Decimal(0),
            cfo=Decimal(30),
            capex=Decimal(12),
            dfc_period_start=jan,
        ),
        _q(  # DRE Q2 isolated (Apr-Jun); DFC YTD 6m
            date(2025, 6, 30),
            revenue=Decimal(110),
            period_start=date(2025, 4, 1),
            dep_amort=Decimal(25),
            dividends_paid=Decimal(40),
            cfo=Decimal(70),
            capex=Decimal(30),
            dfc_period_start=jan,
        ),
        _q(  # DRE Q3 isolated (Jul-Sep); DFC YTD 9m
            date(2025, 9, 30),
            revenue=Decimal(120),
            period_start=date(2025, 7, 1),
            dep_amort=Decimal(45),
            dividends_paid=Decimal(40),
            cfo=Decimal(120),
            capex=Decimal(50),
            dfc_period_start=jan,
        ),
    ]
    annual = _q(
        date(2025, 12, 31),
        revenue=Decimal(500),
        dep_amort=Decimal(70),
        dividends_paid=Decimal(60),
        cfo=Decimal(200),
        capex=Decimal(80),
        equity=Decimal(8000),
    )

    ttm = build_ttm(quarters, annual)

    assert ttm is not None
    assert ttm.revenue == Decimal(500)  # DRE: 100+110+120 + Q4(170)
    # DFC isolated: 10, 15, 20, and Q4 D&A = 70 - 45 = 25 -> full year 70.
    assert ttm.dep_amort == Decimal(70)
    # DFC isolated: 0, 40, 0, and Q4 dividends = 60 - 40 = 20 -> full year 60.
    assert ttm.dividends_paid == Decimal(60)
    # CFO isolated on the DFC span: 30, 40, 50, Q4 = 200 - 120 = 80 -> full year 200.
    assert ttm.cfo == Decimal(200)
    # Capex isolated: 12, 18, 20, Q4 = 80 - 50 = 30 -> full year 80.
    assert ttm.capex == Decimal(80)


def test_ttm_returns_none_with_fewer_than_four_quarters() -> None:
    quarters = [_q(e, revenue=Decimal(1000)) for e in _ENDS[:3]]
    assert build_ttm(quarters, None) is None


def test_ttm_flow_is_null_when_a_quarter_lacks_the_line() -> None:
    quarters = [_q(e, revenue=Decimal(1000), net_income=Decimal(100)) for e in _ENDS]
    quarters[1] = _q(_ENDS[1], revenue=Decimal(1000))  # this quarter has no net income

    ttm = build_ttm(quarters, None)

    assert ttm is not None
    assert ttm.revenue == Decimal(4000)  # revenue present in all four
    assert ttm.net_income is None  # a gap makes the TTM flow null, not understated
