"""CLI presentation helpers (pure functions)."""

from datetime import UTC, date, datetime
from decimal import Decimal

from smaug.analysis.application.doctor import (
    DoctorReport,
    ExerciseCoverage,
    IndicatorCoverage,
    TickerCoverage,
)
from smaug.analysis.domain.entities import VIEW_CLOSED_YEAR, TickerAnalysis
from smaug.analysis.domain.indicators import Indicators, NullReason
from smaug.entrypoints.cli import (
    _format_collection_log,
    format_analysis,
    format_doctor,
    format_report,
)
from smaug.ingestion.application.ingest import FetchOutcome, OutcomeStatus
from smaug.ingestion.application.report import CompletenessReportUseCase
from smaug.portfolio.domain.sectors import Sector
from tests.fakes import FakeRawIngestionRepository, load_fixture, make_snapshot


def test_should_render_collection_log_with_summary() -> None:
    outcomes = [
        FetchOutcome("PETR4", "financialData", OutcomeStatus.STORED, 200, "ok"),
        FetchOutcome("BBAS3", "financialData", OutcomeStatus.SKIPPED, 404, "nope"),
    ]

    log = _format_collection_log(outcomes)

    assert "Collection log" in log
    assert "stored=1" in log
    assert "skipped=1" in log


def test_should_render_doctor_coverage_with_named_and_unclassified() -> None:
    report = DoctorReport(
        tickers=(
            TickerCoverage(
                ticker="BBAS3",
                sector=Sector.BANK,
                exercises=(
                    ExerciseCoverage(
                        view=VIEW_CLOSED_YEAR,
                        reference_date=date(2024, 12, 31),
                        indicators=(
                            IndicatorCoverage("roe", True, None),
                            IndicatorCoverage("pe", False, NullReason.MISSING_PRICE),
                            IndicatorCoverage("net_margin", False, None),
                        ),
                    ),
                ),
            ),
            TickerCoverage(ticker="TAEE11", sector=Sector.UTILITY, exercises=()),
        )
    )

    out = format_doctor(report)

    # A named null surfaces its cause; an unclassified null is flagged, never dropped.
    assert "missing_price" in out
    assert "net_margin" in out
    assert "unclassified" in out
    assert "missing_price=1" in out  # breakdown tallies the named cause
    assert "(no persisted analysis)" in out  # a ticker with nothing is still reported


def test_should_render_analysis_with_view_tag() -> None:
    analyses = [
        TickerAnalysis(
            ticker="PETR4",
            sector=Sector.COMMODITY,
            reference_date=date(2024, 12, 31),
            computed_at=datetime(2026, 7, 8, tzinfo=UTC),
            indicators=Indicators(pe=Decimal("11.4")),
            price=Decimal("38.20"),
            price_adjusted=Decimal("30.48"),
            price_basis="nominal_year_avg",
            view="closed_year",
        )
    ]

    text = format_analysis(analyses)

    assert "closed_year" in text
    assert "2024-12-31" in text
    assert "nominal_year_avg" in text


async def test_should_render_report_with_missing_marker() -> None:
    repo = FakeRawIngestionRepository()
    await repo.add(
        make_snapshot(
            "PETR4",
            "incomeStatementHistoryQuarterly",
            load_fixture("petr4_income_quarterly.json"),
        )
    )
    report = await CompletenessReportUseCase(
        repo, ["incomeStatementHistoryQuarterly", "financialData"]
    ).execute(["PETR4"])

    text = format_report(report)

    assert "PETR4" in text
    assert "MISSING" in text
    assert "totalDebt" in text
