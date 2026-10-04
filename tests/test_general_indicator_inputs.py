"""General indicators preserve period scaling and missing-input semantics."""

from dataclasses import replace
from datetime import date
from decimal import Decimal

from smaug.analysis.domain.calculator import compute
from smaug.analysis.domain.financials import MarketData, StandardizedFinancials
from smaug.analysis.domain.indicators import NullReason
from smaug.analysis.infrastructure.free_float import filed_free_float
from smaug.portfolio.domain.sectors import Sector


def test_flow_indicators_match_equivalent_annual_and_half_year_inputs() -> None:
    half = StandardizedFinancials(
        reference_date=date(2025, 6, 30),
        sector=Sector.INDUSTRY,
        period_start=date(2025, 1, 1),
        net_income=Decimal(20),
        revenue=Decimal(100),
        cfo=Decimal(40),
        capex=Decimal(10),
        current_assets=Decimal(100),
        current_liabilities=Decimal(50),
        inventories=Decimal(20),
    )
    annual = replace(
        half,
        reference_date=date(2025, 12, 31),
        net_income=Decimal(40),
        revenue=Decimal(200),
        cfo=Decimal(80),
        capex=Decimal(20),
    )
    market = MarketData(market_cap=Decimal(1000))
    first, second = compute(half, None, market), compute(annual, None, market)
    for name in (
        "price_to_cfo",
        "cfo_yield",
        "cfo_margin",
        "fcf_margin",
        "cash_conversion",
        "capex_to_cfo",
    ):
        assert getattr(first, name) is not None
        assert getattr(first, name) == getattr(second, name)
    missing = compute(replace(half, inventories=None), None, market)
    assert first.quick_ratio == Decimal("1.6")
    assert missing.current_ratio == Decimal(2)
    assert missing.quick_ratio is None
    assert missing.null_reasons["quick_ratio"] is NullReason.SOURCE_ACCOUNT_ABSENT


def test_free_float_selects_filed_versions_without_using_a_future_assembly() -> None:
    def disclosure(version: int, percent: str, assembly: str) -> dict[str, object]:
        return {
            "module": "FREE_FLOAT",
            "payload": {
                "reference_date": "2025-12-31",
                "version": version,
                "company_free_float_percent": percent,
                "assembly_date": assembly,
            },
        }

    value, evidence = filed_free_float(
        [
            disclosure(1, "40", "2025-04-01"),
            disclosure(2, "45", "2025-04-01"),
            disclosure(3, "50", "2025-09-01"),
        ],
        date(2025, 6, 30),
    )
    assert value == Decimal("0.45")
    assert "version=2" in evidence.expected
    absent, absent_evidence = filed_free_float([], date(2025, 6, 30))
    assert absent is None
    assert absent_evidence.blocker is NullReason.SOURCE_ACCOUNT_ABSENT
