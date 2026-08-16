"""The read API publishes the basis behind strict and convention metrics."""

from datetime import UTC, date, datetime
from decimal import Decimal

from smaug.analysis.domain.entities import (
    VIEW_CLOSED_YEAR,
    VIEW_TTM,
    AnalysisView,
    TickerAnalysis,
)
from smaug.analysis.domain.indicators import (
    INDICATOR_CONTRACT,
    Indicators,
    IndicatorTier,
    indicator_names,
)
from smaug.entrypoints.api import _to_response
from smaug.portfolio.domain.taxonomy import Classification


def _analysis(view: AnalysisView) -> TickerAnalysis:
    return TickerAnalysis(
        ticker="PETR4",
        classification=Classification(
            "Petróleo", "Petróleo, Gás e Biocombustíveis", None
        ),
        reference_date=date(2025, 12, 31),
        computed_at=datetime(2026, 8, 15, tzinfo=UTC),
        view=view,
        price=Decimal("38"),
        price_basis="b3_latest_close",
        share_count_basis="cvm_latest_filed_outstanding_current_base",
        indicators=Indicators(
            pe_basic=Decimal("6"),
            pb=Decimal("1.4"),
            company_pe=Decimal("7"),
            company_pb=Decimal("1.6"),
        ),
    )


def test_api_contract_distinguishes_strict_and_market_convention_bases() -> None:
    response = _to_response(_analysis(VIEW_TTM))

    strict_pe = response.indicator_contract["pe_basic"]
    assert strict_pe.tier is IndicatorTier.STRICT
    assert strict_pe.basis == "security_cpc41"
    assert strict_pe.numerator == "security_price"
    assert strict_pe.denominator == "cpc41_basic_eps"
    assert strict_pe.reference_period == "last_twelve_months"
    assert strict_pe.share_basis == "cpc41_weighted_average_class_rights"
    assert strict_pe.provenance == ["cvm", "b3"]

    market_pe = response.indicator_contract["pe_basic_market"]
    assert market_pe.tier is IndicatorTier.MARKET_CONVENTION
    assert market_pe.basis == "security_market_convention"
    assert market_pe.numerator == "security_price"
    assert market_pe.denominator == "market_convention_basic_eps"
    assert market_pe.share_basis == "analysis.share_count_basis"

    company_pe = response.indicator_contract["company_pe"]
    assert company_pe.tier is IndicatorTier.MARKET_CONVENTION
    assert company_pe.basis == "company_market_convention"
    assert company_pe.numerator == "market_capitalization"
    assert company_pe.denominator == "attributable_net_income"
    assert company_pe.reference_period == "last_twelve_months"
    assert company_pe.price_basis == "analysis.price_basis"
    assert company_pe.share_basis == "listed_classes_outstanding"

    company_pb = response.indicator_contract["company_pb"]
    assert company_pb.denominator == "current_attributable_equity"
    assert company_pb.reference_period == "reference_date_closing"


def test_api_contract_names_closed_year_period_without_changing_formula() -> None:
    response = _to_response(_analysis(VIEW_CLOSED_YEAR))

    assert (
        response.indicator_contract["company_pe"].reference_period
        == "closed_fiscal_year"
    )
    assert response.indicator_contract["pe_basic"].reference_period == (
        "closed_fiscal_year"
    )


def test_contract_only_names_persisted_indicator_fields() -> None:
    assert set(INDICATOR_CONTRACT) <= set(indicator_names())
