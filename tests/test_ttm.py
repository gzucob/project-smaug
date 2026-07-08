"""TTM assembly: sum isolated quarter flows, latest stocks, derive the missing Q4."""

from datetime import date
from decimal import Decimal

from smaug.analysis.domain.financials import StandardizedFinancials
from smaug.analysis.domain.ttm import build_ttm
from smaug.portfolio.domain.sectors import Sector


def _q(
    end: date,
    *,
    revenue: Decimal | None = None,
    net_income: Decimal | None = None,
    equity: Decimal | None = None,
    period_start: date | None = None,
) -> StandardizedFinancials:
    return StandardizedFinancials(
        reference_date=end,
        sector=Sector.COMMODITY,
        period_start=period_start,
        revenue=revenue,
        net_income=net_income,
        equity=equity,
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
