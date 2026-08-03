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
from smaug.analysis.domain.indicators import Indicators, NullReason
from smaug.entrypoints.cli import (
    _format_collection_log,
    format_analysis,
    format_analysis_run,
    format_doctor,
    format_doctor_summary,
    format_drift_summary,
    format_report,
)
from smaug.ingestion.application.ingest import FetchOutcome, OutcomeStatus
from smaug.ingestion.application.report import CompletenessReportUseCase
from smaug.portfolio.domain.sectors import Sector
from smaug.portfolio.domain.taxonomy import Classification
from tests.fakes import FakeRawIngestionRepository, make_snapshot


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
            classification=Classification(
                "Petróleo, Gás e Biocombustíveis",
                "Petróleo, Gás e Biocombustíveis",
                "Exploração, Refino e Distribuição",
            ),
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
            "DRE",
            {"accounts": [{"code": "3.01", "name": "Receita de Venda de Bens"}]},
        )
    )
    report = await CompletenessReportUseCase(repo, ["DRE", "BPA"]).execute(["PETR4"])

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
        indicators=Indicators(pe=Decimal("11.4")),
        price=Decimal("10"),
        price_basis="nominal_year_avg",
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
                IndicatorCoverage("pe", False, NullReason.MISSING_PRICE),
            ),
            _coverage(
                "BBBB3",
                IndicatorCoverage("pe", False, NullReason.MISSING_PRICE),
                IndicatorCoverage("net_margin", False, None),
            ),
            TickerCoverage(ticker="CCCC3", sector=Sector.INDUSTRY, exercises=()),
        )
    )

    out = format_doctor_summary(report)

    assert "missing_price" in out
    assert "2 of 3 ticker(s) analyzed" in out
    assert "value=1" in out
    assert "unclassified=1" in out
    assert "BBBB3" in out  # the ticker carrying it is named
    assert "AAAA3" not in out  # a fully named ticker is a number, not a line
    assert "no persisted analysis: CCCC3" in out


def test_doctor_summary_says_so_when_every_null_is_named() -> None:
    report = DoctorReport(
        tickers=(
            _coverage(
                "AAAA3",
                IndicatorCoverage("roe", True, None),
                IndicatorCoverage("pe", False, NullReason.MISSING_PRICE),
            ),
        )
    )

    assert "every null carries a named cause." in format_doctor_summary(report)


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
