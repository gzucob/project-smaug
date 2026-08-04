"""Completeness report: filed-account counts and sector anchors (plan §6)."""

from datetime import UTC, datetime

from smaug.ingestion.application.report import CompletenessReportUseCase
from tests.fakes import FakeRawIngestionRepository, fake_sector_resolver, make_snapshot


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
        repo, ["BPA", "BPP", "DRE", "DFC"], sector_resolver=fake_sector_resolver
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
        repo, ["BPA", "BPP", "DRE", "DFC"], sector_resolver=fake_sector_resolver
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
