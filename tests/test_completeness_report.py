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


async def test_cvm_report_counts_accounts_and_checks_bank_anchors() -> None:
    repo = FakeRawIngestionRepository()
    await repo.add(
        make_snapshot(
            "BBAS3",
            "BPA",
            {"accounts": [{"code": "1", "name": "Ativo Total"}, {"code": "1.01"}]},
        )
    )
    await repo.add(
        make_snapshot(
            "BBAS3",
            "BPP",
            {"accounts": [{"code": "2.07", "name": "Patrimônio Líquido Consolidado"}]},
        )
    )
    await repo.add(
        make_snapshot(
            "BBAS3",
            "DRE",
            {
                "accounts": [
                    {"code": "3.01", "name": "Receitas de Intermediação Financeira"},
                    {"code": "3.07", "name": "Lucro das Operações Continuadas"},
                ]
            },
        )
    )

    report = await CompletenessReportUseCase(
        repo, ["BPA", "BPP", "DRE", "DFC"], source="cvm"
    ).execute(["BBAS3"])

    assert report.depth_label == "accounts"
    ticker_report = report.tickers[0]
    assert ticker_report.max_quarters == 2  # BPA/DRE each carry two accounts

    presence = {m.module: m.present for m in ticker_report.modules}
    assert presence["DFC"] is False  # never collected -> a discovery

    present = set(ticker_report.sector_check.present_fields)
    assert present == {
        "Ativo Total",
        "Patrimônio Líquido",
        "Resultado do período",
        "Receita de intermediação",
    }


async def test_cvm_report_flags_holding_insurer_missing_seguros() -> None:
    # Caixa Seguridade files as a holding (commercial layout), not as an insurer.
    repo = FakeRawIngestionRepository()
    await repo.add(
        make_snapshot(
            "CXSE3",
            "DRE",
            {"accounts": [{"code": "3.01", "name": "Receita de Venda de Bens"}]},
        )
    )

    report = await CompletenessReportUseCase(
        repo, ["BPA", "BPP", "DRE", "DFC"], source="cvm"
    ).execute(["CXSE3"])

    missing = report.tickers[0].sector_check.missing_fields
    assert "Receita de seguros" in missing


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
