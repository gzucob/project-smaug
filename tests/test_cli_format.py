"""CLI presentation helpers (pure functions)."""

from smaug.entrypoints.cli import _format_collection_log, format_report
from smaug.ingestion.application.ingest import FetchOutcome, OutcomeStatus
from smaug.ingestion.application.report import CompletenessReportUseCase
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
