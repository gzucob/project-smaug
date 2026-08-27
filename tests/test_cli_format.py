"""CLI presentation helpers (pure functions)."""

from datetime import UTC, date, datetime
from decimal import Decimal

from smaug.analysis.application.analyze import (
    AnalysisRun,
    AnalysisStatus,
    TickerOutcome,
)
from smaug.analysis.application.doctor import (
    DoctorReport,
    ExerciseCoverage,
    IndicatorCoverage,
    TickerCoverage,
)
from smaug.analysis.application.drift import AccountDrift, DriftReport, TickerDrift
from smaug.analysis.domain.entities import VIEW_CLOSED_YEAR, TickerAnalysis
from smaug.analysis.domain.financials import (
    AccountingRegime,
    Cpc41AccountEvidence,
    Cpc41EvidenceStatus,
    Cpc41PeriodProvenance,
    Cpc41SelectionStatus,
    Cpc41WindowProvenance,
    DebtBlocker,
    DebtCoverageEvidence,
    DebtEvidenceSnapshot,
    RegimeSource,
)
from smaug.analysis.domain.indicators import Indicators, NullReason
from smaug.entrypoints.cli import (
    _format_collection_log,
    format_analysis,
    format_analysis_run,
    format_doctor,
    format_doctor_summary,
    format_drift_summary,
    format_ingestion_metrics,
    format_ingestion_runs,
    format_ingestion_validations,
    format_report,
)
from smaug.ingestion.application.ingest import FetchOutcome, OutcomeStatus
from smaug.ingestion.application.report import CompletenessReportUseCase
from smaug.ingestion.domain.runs import (
    IngestionRun,
    IngestionRunCounts,
    IngestionRunMetrics,
    IngestionRunParameters,
    IngestionRunStatus,
    ParserIdentity,
    TickerScope,
)
from smaug.ingestion.domain.validation import (
    BatchValidationStatus,
    IngestionValidationReport,
    SourceBatchValidation,
    ValidationFinding,
    ValidationRule,
)
from smaug.portfolio.domain.sectors import Sector
from smaug.portfolio.domain.taxonomy import Classification
from tests.fakes import FakeRawIngestionRepository, fake_sector_resolver, make_snapshot


def test_should_render_collection_log_with_summary() -> None:
    outcomes = [
        FetchOutcome("PETR4", "financialData", OutcomeStatus.STORED, 200, "ok"),
        FetchOutcome("BBAS3", "financialData", OutcomeStatus.SKIPPED, 404, "nope"),
    ]

    log = _format_collection_log(outcomes)

    assert "Collection log" in log
    assert "stored=1" in log
    assert "skipped=1" in log


def test_should_render_persisted_run_provenance_and_incomplete_marker() -> None:
    run = IngestionRun(
        run_id="run-123",
        started_at=datetime(2026, 8, 6, 12, 0, tzinfo=UTC),
        ended_at=None,
        status=IngestionRunStatus.RUNNING,
        parameters=IngestionRunParameters(
            ticker_scope=TickerScope.ALL,
            tickers=("PETR4", "VALE3"),
            years=(2023, 2024),
            document="DFP",
            modules=("DRE", "BPA"),
            force=False,
            verbose=False,
        ),
        application_commit="abc123",
        parsers=(ParserIdentity("cvm.statements.csv", 1),),
        counts=IngestionRunCounts(planned=10, stored=3, unchanged=1, skipped=1),
    )

    output = format_ingestion_runs((run,))

    assert "run-123  running (incomplete)" in output
    assert "scope=all tickers=2 [PETR4, VALE3]" in output
    assert "document=DFP years=2023,2024" in output
    assert "commit=abc123" in output
    assert "cvm.statements.csv@1" in output
    assert "calls=5/10 excluded=0 remaining=5" in output
    assert "unchanged=1" in output


def test_ingestion_metrics_include_throughput_volume_and_cache_behavior() -> None:
    run = IngestionRun(
        run_id="run-123",
        started_at=datetime(2026, 8, 6, 12, 0, tzinfo=UTC),
        ended_at=datetime(2026, 8, 6, 12, 0, 2, tzinfo=UTC),
        status=IngestionRunStatus.COMPLETED,
        parameters=IngestionRunParameters(
            ticker_scope=TickerScope.EXPLICIT,
            tickers=("PETR4",),
            years=(2024,),
            document="DFP",
            modules=("DRE",),
            force=False,
            verbose=False,
            concurrency=8,
        ),
        application_commit="abc123",
        parsers=(),
        counts=IngestionRunCounts(planned=4, stored=4),
        metrics=IngestionRunMetrics(
            source_seconds=1.25,
            download_seconds=0.5,
            parse_seconds=1.0,
            store_seconds=0.25,
            payload_bytes=120,
            archive_bytes=1024,
            rows=4,
            cache_hits=3,
            cache_misses=1,
        ),
    )

    output = format_ingestion_metrics(run)

    assert "elapsed=2.000s" in output
    assert "throughput=2.00 calls/s" in output
    assert "rows=4" in output
    assert "archive_bytes=1024" in output
    assert "cache_hit=3 cache_miss=1" in output


def test_should_render_quarantine_evidence_and_reprocessing_guidance() -> None:
    report = IngestionValidationReport(
        report_id="validation-123",
        run_id="run-123",
        recorded_at=datetime(2026, 8, 10, tzinfo=UTC),
        status=BatchValidationStatus.QUARANTINED,
        validation=SourceBatchValidation(
            source="cvm",
            batch="dfp_cia_aberta_2024.zip",
            module="DRE",
            artifact_id="sha256:" + "a" * 64,
            parser=ParserIdentity("cvm.statements.csv", 1),
            rules=(ValidationRule("csv-schema", 1),),
            findings=(ValidationFinding("csv-schema", "DRE lacks VL_CONTA"),),
        ),
    )

    output = format_ingestion_validations((report,))

    assert "validation-123  quarantined run=run-123" in output
    assert "csv-schema@1" in output
    assert "DRE lacks VL_CONTA" in output
    assert "rerun the same ingest command with --force" in output


def test_should_render_cash_row_reconciliation_without_dumping_raw_evidence() -> None:
    report = IngestionValidationReport(
        report_id="validation-cash",
        run_id="run-123",
        recorded_at=datetime(2026, 8, 10, tzinfo=UTC),
        status=BatchValidationStatus.ACCEPTED,
        validation=SourceBatchValidation(
            source="b3",
            batch="GetListedCashDividends:BBDC",
            module="CASH_DIVIDEND_B3",
            parser=ParserIdentity("b3.cash-dividends.json", 2),
            rules=(ValidationRule("row-reconciliation", 1),),
            observations={
                "rows": 4,
                "fetched": 4,
                "accepted": 2,
                "rejected": 1,
                "deduplicated": 1,
                "coverage_established": False,
            },
            evidence={"rejected_rows": [{"row": 4}], "deduplicated_rows": [{"row": 3}]},
        ),
    )

    output = format_ingestion_validations((report,))

    assert "reconciliation=fetched=4 accepted=2 rejected=1 deduplicated=1" in output
    assert "coverage_established=False" in output
    assert "evidence=rejected_rows count=1" in output
    assert "evidence=deduplicated_rows count=1" in output
    assert '"row": 4' not in output


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
                            IndicatorCoverage(
                                "pe_basic", False, NullReason.MISSING_PRICE
                            ),
                            IndicatorCoverage("net_margin", False, None),
                        ),
                        price_source_code="AZZA3",
                        price_source_session=date(2026, 8, 14),
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
    assert "price_source=B3:AZZA3@2026-08-14" in out
    assert "(no persisted analysis)" in out  # a ticker with nothing is still reported


def test_should_render_expected_cpc41_refs_and_basis_statuses() -> None:
    provenance = Cpc41WindowProvenance(
        selected_periods=(
            Cpc41PeriodProvenance(
                reference_date=date(2024, 12, 31),
                disclosure_status=Cpc41EvidenceStatus.AMBIGUOUS,
                class_status=Cpc41EvidenceStatus.AMBIGUOUS,
                multiplier_status=Cpc41EvidenceStatus.ABSENT,
                basic_disclosure_status=Cpc41EvidenceStatus.ABSENT,
                diluted_disclosure_status=Cpc41EvidenceStatus.AMBIGUOUS,
                basic_class_status=Cpc41EvidenceStatus.ABSENT,
                diluted_class_status=Cpc41EvidenceStatus.AMBIGUOUS,
                basic_multiplier_status=Cpc41EvidenceStatus.ABSENT,
                diluted_multiplier_status=Cpc41EvidenceStatus.AMBIGUOUS,
                source_accounts=(
                    Cpc41AccountEvidence(
                        module="DRE",
                        code="3.99.01.*",
                        name="class label required",
                        selection_status=Cpc41SelectionStatus.ABSENT,
                        basis="basic",
                        expected=True,
                    ),
                ),
            ),
        ),
        basic_blocker=NullReason.MISSING_CPC41_DISCLOSURE,
    )
    report = DoctorReport(
        tickers=(
            TickerCoverage(
                ticker="PETR4",
                sector=Sector.COMMODITY,
                exercises=(
                    ExerciseCoverage(
                        view=VIEW_CLOSED_YEAR,
                        reference_date=date(2024, 12, 31),
                        indicators=(),
                        cpc41_window_provenance=provenance,
                    ),
                ),
            ),
        )
    )

    out = format_doctor(report)

    assert "basic=absent/absent/absent/absent" in out
    assert "diluted=ambiguous/ambiguous/ambiguous/absent" in out
    assert "module=DRE code=3.99.01.*" in out
    assert "name='class label required'" in out
    assert "selection=absent expected=true" in out


def test_should_render_analysis_with_view_tag() -> None:
    analyses = [
        TickerAnalysis(
            ticker="PETR4",
            classification=Classification(
                "Petróleo, Gás e Biocombustíveis",
                "Petróleo, Gás e Biocombustíveis",
                "Exploração, Refino e Distribuição",
            ),
            reference_date=date(2024, 12, 31),
            computed_at=datetime(2026, 7, 8, tzinfo=UTC),
            indicators=Indicators(pe_basic=Decimal("11.4")),
            price=Decimal("38.20"),
            price_adjusted=Decimal("30.48"),
            price_basis="b3_year_end_close",
            view="closed_year",
        )
    ]

    text = format_analysis(analyses)

    assert "closed_year" in text
    assert "2024-12-31" in text
    assert "b3_year_end_close" in text


async def test_should_render_report_with_missing_marker() -> None:
    repo = FakeRawIngestionRepository()
    await repo.add(
        make_snapshot(
            "PETR4",
            "DRE",
            {"accounts": [{"code": "3.01", "name": "Receita de Venda de Bens"}]},
        )
    )
    report = await CompletenessReportUseCase(
        repo, ["DRE", "BPA"], sector_resolver=fake_sector_resolver
    ).execute(["PETR4"])

    text = format_report(report)

    assert "PETR4" in text
    assert "MISSING" in text
    # The anchor the DRE alone cannot answer: the balance sheet was not collected.
    assert "Ativo Total" in text


def _analysis() -> TickerAnalysis:
    return TickerAnalysis(
        ticker="AAAA3",
        classification=Classification("Bens Industriais", "Máquinas", "Motores"),
        reference_date=date(2024, 12, 31),
        computed_at=datetime(2026, 7, 30, tzinfo=UTC),
        indicators=Indicators(pe_basic=Decimal("11.4")),
        price=Decimal("10"),
        price_basis="b3_year_end_close",
        view="closed_year",
    )


def _coverage(ticker: str, *cells: IndicatorCoverage) -> TickerCoverage:
    return TickerCoverage(
        ticker=ticker,
        sector=Sector.INDUSTRY,
        exercises=(
            ExerciseCoverage(
                view=VIEW_CLOSED_YEAR,
                reference_date=date(2024, 12, 31),
                indicators=cells,
            ),
        ),
    )


def test_doctor_summary_counts_causes_but_still_names_an_unclassified_null() -> None:
    # At exchange scale the per-cell listing is unreadable, so it is summarized —
    # except for the one finding that asks for work, which is named per ticker.
    report = DoctorReport(
        tickers=(
            _coverage(
                "AAAA3",
                IndicatorCoverage("roe", True, None),
                IndicatorCoverage("pe_basic", False, NullReason.MISSING_PRICE),
            ),
            _coverage(
                "BBBB3",
                IndicatorCoverage("pe_basic", False, NullReason.MISSING_PRICE),
                IndicatorCoverage("net_margin", False, None),
            ),
            TickerCoverage(ticker="CCCC3", sector=Sector.INDUSTRY, exercises=()),
        )
    )

    out = format_doctor_summary(report)

    assert "missing_price" in out
    assert "2 of 3 ticker(s) analyzed" in out
    assert "value=1" in out
    assert "price provenance=0/2 exercises" in out
    assert "unclassified=1" in out
    assert "BBBB3" in out  # the ticker carrying it is named
    assert "AAAA3" not in out  # a fully named ticker is a number, not a line
    assert "no persisted analysis: CCCC3" in out
    assert "requested=3 persisted=2 no-analysis=1 stale=0 legacy=0" in out
    assert "cells: total=4 values=1 nulls=3" in out
    assert "missing_or_recoverable=2 (66.7% of nulls; 50.0% of all cells)" in out


def test_doctor_summary_says_so_when_every_null_is_named() -> None:
    report = DoctorReport(
        tickers=(
            _coverage(
                "AAAA3",
                IndicatorCoverage("roe", True, None),
                IndicatorCoverage("pe_basic", False, NullReason.MISSING_PRICE),
            ),
        )
    )

    assert "every null carries a named cause." in format_doctor_summary(report)


def test_doctor_reconciles_debt_decisions_with_dependent_indicator_cells() -> None:
    evidence = DebtCoverageEvidence(
        regime=AccountingRegime.CORPORATE,
        regime_source=RegimeSource.FILED,
        primary_blocker=DebtBlocker.INCOMPLETE_DEBT_COVERAGE,
        secondary_blockers=(DebtBlocker.MISSING_NON_CURRENT_AGGREGATE,),
    )
    report = DoctorReport(
        tickers=(
            TickerCoverage(
                ticker="AAAA3",
                sector=Sector.INDUSTRY,
                exercises=(
                    ExerciseCoverage(
                        view=VIEW_CLOSED_YEAR,
                        reference_date=date(2024, 12, 31),
                        indicators=(
                            IndicatorCoverage(
                                "net_debt",
                                False,
                                NullReason.INCOMPLETE_DEBT_COVERAGE,
                            ),
                        ),
                        debt_evidence=evidence,
                        debt_evidence_snapshot=DebtEvidenceSnapshot.HISTORICAL,
                    ),
                ),
            ),
        )
    )

    out = format_doctor_summary(report)

    assert "persisted decisions=1 incomplete=1" in out
    assert "dependent indicator cells with incomplete_debt_coverage=1" in out
    assert "legacy snapshots=0 unclassified blockers=0" in out
    assert "one debt decision per persisted row" in out


def test_analysis_run_summary_names_a_failure_and_counts_the_rest() -> None:
    run = AnalysisRun(
        outcomes=(
            TickerOutcome("AAAA3", AnalysisStatus.ANALYZED, (_analysis(),)),
            TickerOutcome("BBBB3", AnalysisStatus.SKIPPED, (), "no CVM fundamentals"),
            TickerOutcome("CCCC3", AnalysisStatus.ERROR, (), "ValueError: boom"),
        )
    )

    out = format_analysis_run(run)

    assert "!! CCCC3" in out
    assert "ValueError: boom" in out
    assert "skipped (nothing mirrored): BBBB3" in out
    assert "3 ticker(s), 1 view(s) stored" in out
    assert "analyzed=1" in out


def _drift(ticker: str, account: str, read: tuple[int, ...], missing: tuple[int, ...]):
    return TickerDrift(
        ticker=ticker,
        years=tuple(sorted(read + missing)),
        accounts=(
            AccountDrift(account=account, read=read, missing=missing, boundaries=1),
        ),
    )


def test_drift_summary_rolls_up_per_account_with_the_urgent_side_first() -> None:
    # One filer's account drifting is a fact about that filer; the same account
    # drifting across many of them is one bug in our mapping.
    report = DriftReport(
        tickers=(
            _drift("AAAA3", "capex", read=(2021, 2022), missing=(2019, 2020)),
            _drift("BBBB3", "capex", read=(2021, 2022), missing=(2019, 2020)),
            _drift("CCCC3", "ebitda", read=(2019, 2020), missing=(2021, 2022)),
        )
    )

    out = format_drift_summary(report)

    # ebitda stopped mapping in the filings we read today, so it outranks capex,
    # which merely never reached the old chart.
    assert out.index("ebitda") < out.index("capex")
    assert "3 account/ticker pairs changed status across 2 account(s)" in out


def test_drift_summary_says_so_when_nothing_drifted() -> None:
    out = format_drift_summary(DriftReport(tickers=()))

    assert "no account changed status" in out
