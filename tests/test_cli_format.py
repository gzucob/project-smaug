"""CLI presentation helpers (pure functions)."""

from datetime import UTC, date, datetime
from decimal import Decimal

from smaug.analysis.domain.entities import TickerAnalysis
from smaug.analysis.domain.indicators import Indicators
from smaug.entrypoints.cli import (
    _format_collection_log,
    format_analysis,
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


def test_should_render_analysis_with_view_tag() -> None:
    analyses = [
        TickerAnalysis(
            ticker="PETR4",
            sector=Sector.COMMODITY,
            reference_date=date(2024, 12, 31),
            computed_at=datetime(2026, 7, 8, tzinfo=UTC),
            indicators=Indicators(pe=Decimal("11.4")),
            price=Decimal("30.48"),
            price_nominal=Decimal("38.20"),
            price_basis="adjusted_year_avg",
            view="closed_year",
        )
    ]

    text = format_analysis(analyses)

    assert "closed_year" in text
    assert "2024-12-31" in text
    assert "adjusted_year_avg" in text


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
