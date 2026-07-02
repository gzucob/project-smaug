"""Completeness report: quarter counts and sector-directed checks (plan §6)."""

from datetime import UTC, datetime

from smaug.ingestion.application.report import CompletenessReportUseCase
from smaug.portfolio.domain.sectors import Sector
from tests.fakes import FakeRawIngestionRepository, load_fixture, make_snapshot


async def test_should_count_quarters_and_flag_missing_field_for_commodity() -> None:
    repo = FakeRawIngestionRepository()
    payload = load_fixture("petr4_income_quarterly.json")
    await repo.add(make_snapshot("PETR4", "incomeStatementHistoryQuarterly", payload))

    use_case = CompletenessReportUseCase(
        repo, ["incomeStatementHistoryQuarterly", "financialData"]
    )
    report = await use_case.execute(["PETR4"])
    ticker_report = report.tickers[0]

    assert ticker_report.sector is Sector.COMMODITY
    assert ticker_report.max_quarters == 4

    presence = {m.module: m.present for m in ticker_report.modules}
    assert presence["incomeStatementHistoryQuarterly"] is True
    assert presence["financialData"] is False

    # totalDebt lives in financialData, which was not collected -> a discovery.
    assert "totalDebt" in ticker_report.sector_check.missing_fields
    assert "totalRevenue" in ticker_report.sector_check.present_fields


async def test_should_verify_bank_specific_fields_across_modules() -> None:
    repo = FakeRawIngestionRepository()
    await repo.add(
        make_snapshot(
            "BBAS3",
            "financialData",
            {
                "results": [
                    {"financialData": {"returnOnEquity": 0.2, "netIncome": 1000}}
                ]
            },
        )
    )
    await repo.add(
        make_snapshot(
            "BBAS3",
            "balanceSheetHistoryQuarterly",
            {"results": [{"balanceSheetHistory": [{"totalStockholderEquity": 5000}]}]},
        )
    )

    use_case = CompletenessReportUseCase(
        repo, ["financialData", "balanceSheetHistoryQuarterly"]
    )
    report = await use_case.execute(["BBAS3"])
    ticker_report = report.tickers[0]

    assert ticker_report.sector is Sector.BANK
    assert set(ticker_report.sector_check.present_fields) == {
        "totalStockholderEquity",
        "netIncome",
        "returnOnEquity",
    }
    assert ticker_report.sector_check.missing_fields == ()


async def test_should_read_latest_snapshot_when_multiple_revisions_exist() -> None:
    repo = FakeRawIngestionRepository()
    await repo.add(
        make_snapshot(
            "PETR4",
            "financialData",
            {"results": [{"financialData": {"totalRevenue": 1}}]},
            fetched_at=datetime(2026, 1, 1, tzinfo=UTC),
        )
    )
    await repo.add(
        make_snapshot(
            "PETR4",
            "financialData",
            {"results": [{"financialData": {"totalRevenue": 2}}]},
            fetched_at=datetime(2026, 6, 1, tzinfo=UTC),
        )
    )

    latest = await repo.find_latest("PETR4", "financialData")

    assert latest is not None
    assert latest.payload["results"][0]["financialData"]["totalRevenue"] == 2
