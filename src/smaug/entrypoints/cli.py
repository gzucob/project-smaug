"""CLI entrypoints — the composition root for Phase 1.

Wires config -> Mongo -> brapi client -> repository -> use cases, and renders
results to stdout. No business logic lives here: the commands only assemble
dependencies and call the use cases (plan §3.1 / CLAUDE.md).
"""

from __future__ import annotations

import asyncio
from collections.abc import Coroutine
from decimal import Decimal
from typing import Any, cast

import httpx
import typer

from smaug.analysis.application.analyze import AnalyzePortfolioUseCase
from smaug.analysis.application.doctor import (
    DoctorReport,
    DoctorUseCase,
    ExerciseCoverage,
)
from smaug.analysis.domain.entities import TickerAnalysis
from smaug.analysis.domain.indicators import NullReason
from smaug.analysis.infrastructure.brapi_price import BrapiPriceProvider
from smaug.analysis.infrastructure.composite_price import CompositePriceProvider
from smaug.analysis.infrastructure.fallback_price import (
    FallbackPriceHistory,
    FallbackQuoteProvider,
)
from smaug.analysis.infrastructure.mongo_capital import MongoSharesReader
from smaug.analysis.infrastructure.mongo_fundamentals import MongoFundamentalsReader
from smaug.analysis.infrastructure.sql_repository import SqlAlchemyAnalysisRepository
from smaug.analysis.infrastructure.yahoo_price import (
    YahooPriceHistory,
    YahooQuoteProvider,
)
from smaug.ingestion.application.ingest import (
    FetchOutcome,
    IngestPortfolioUseCase,
    OutcomeStatus,
)
from smaug.ingestion.application.report import (
    CompletenessReport,
    CompletenessReportUseCase,
    TickerReport,
)
from smaug.ingestion.domain.ports import RawDataSource
from smaug.ingestion.infrastructure.brapi_client import BrapiClient
from smaug.ingestion.infrastructure.cvm_capital import (
    CAPITAL_MODULE,
    CvmCapitalSource,
)
from smaug.ingestion.infrastructure.cvm_source import CvmDataSource, CvmDocument
from smaug.ingestion.infrastructure.repositories import BeanieRawIngestionRepository
from smaug.ingestion.infrastructure.routed_source import RoutedDataSource
from smaug.portfolio.domain.cvm_codes import TICKER_TO_CNPJ, TICKER_TO_CVM_CODE
from smaug.portfolio.domain.sectors import portfolio_tickers
from smaug.shared.config import Settings, get_settings
from smaug.shared.db import init_database
from smaug.shared.errors import UnknownTickerError
from smaug.shared.events import EventBus
from smaug.shared.logging import get_logger
from smaug.shared.sql_db import create_engine, create_session_factory

app = typer.Typer(help="smaug — CVM/brapi ingestion and indicator analysis.")
logger = get_logger("smaug.cli")

_FAILED_STATUSES = frozenset({OutcomeStatus.ERROR, OutcomeStatus.ABORTED})


def _guarded[T](coro: Coroutine[Any, Any, T]) -> T:
    """Run a use-case coroutine, turning an unknown ticker into a clean exit.

    Keeps the raw ``KeyError`` from ``sector_of`` off the terminal — the CLI
    reports a typo (or a not-yet-added ticker) as one line, like the ingestion
    side maps brapi HTTP errors to typed ones.
    """
    try:
        return asyncio.run(coro)
    except UnknownTickerError as exc:
        typer.echo(f"error: {exc}", err=True)
        raise typer.Exit(code=2) from exc


@app.command()
def ingest(
    ticker: list[str] | None = typer.Option(
        None, "--ticker", "-t", help="Ticker to collect (repeatable). Default: all."
    ),
    document: str | None = typer.Option(
        None, "--document", help="CVM document: ITR or DFP (overrides config)."
    ),
    year: int | None = typer.Option(
        None, "--year", help="CVM file year to mirror (overrides config)."
    ),
) -> None:
    """Collect the configured modules for the active source and store the mirror."""
    tickers = tuple(ticker) if ticker else portfolio_tickers()
    try:
        exit_code = asyncio.run(_run_ingest(tickers, document=document, year=year))
    except NotImplementedError as exc:
        typer.echo(f"error: {exc}", err=True)
        raise typer.Exit(code=2) from exc
    raise typer.Exit(code=exit_code)


@app.command()
def report(
    ticker: list[str] | None = typer.Option(
        None, "--ticker", "-t", help="Ticker to report (repeatable). Default: all."
    ),
) -> None:
    """Print the completeness report read from the raw mirror."""
    tickers = tuple(ticker) if ticker else portfolio_tickers()
    _guarded(_run_report(tickers))


def _build_data_source(
    settings: Settings,
    http: httpx.AsyncClient,
    *,
    document: str | None = None,
    year: int | None = None,
) -> RawDataSource:
    """Pick the active raw source from config — the brapi/CVM swap seam.

    Both implement ``RawDataSource``, so the use case never knows which one it
    got. The token is only required (and only exists) for brapi. ``document``/
    ``year`` override the config for one run (e.g. to pull several CVM files).
    """
    if settings.ingestion_source == "brapi":
        return BrapiClient(settings.brapi_base_url, settings.require_token(), http)
    doc = (document or settings.cvm_document).upper()
    if doc not in ("ITR", "DFP"):
        raise typer.BadParameter("--document must be ITR or DFP")
    cvm_year = year or settings.cvm_year
    statements = CvmDataSource(
        http,
        TICKER_TO_CVM_CODE,
        year=cvm_year,
        cache_dir=settings.cvm_cache_dir,
        document=cast(CvmDocument, doc),
    )
    # The share counts live in a different CVM archive (FRE), keyed by CNPJ.
    capital = CvmCapitalSource(
        http,
        TICKER_TO_CNPJ,
        year=cvm_year,
        cache_dir=settings.cvm_cache_dir,
    )
    return RoutedDataSource({CAPITAL_MODULE: capital}, default=statements)


async def _run_ingest(
    tickers: tuple[str, ...], *, document: str | None = None, year: int | None = None
) -> int:
    settings = get_settings()
    client = await init_database(settings)
    try:
        async with httpx.AsyncClient(timeout=30.0) as http:
            use_case = IngestPortfolioUseCase(
                client=_build_data_source(settings, http, document=document, year=year),
                repository=BeanieRawIngestionRepository(),
                event_bus=EventBus(),
                modules=settings.active_modules,
                source=settings.ingestion_source,
                delay_seconds=settings.request_delay_seconds,
            )
            outcomes = await use_case.execute(tickers)
    finally:
        await client.close()

    print(_format_collection_log(outcomes))
    return 1 if any(o.status in _FAILED_STATUSES for o in outcomes) else 0


async def _run_report(tickers: tuple[str, ...]) -> None:
    settings = get_settings()
    client = await init_database(settings)
    try:
        use_case = CompletenessReportUseCase(
            repository=BeanieRawIngestionRepository(),
            modules=settings.active_modules,
            source=settings.ingestion_source,
        )
        completeness = await use_case.execute(tickers)
    finally:
        await client.close()

    print(format_report(completeness))


@app.command()
def analyze(
    ticker: list[str] | None = typer.Option(
        None, "--ticker", "-t", help="Ticker to analyze (repeatable). Default: all."
    ),
) -> None:
    """Compute the fundamental + market indicators and store them in Postgres."""
    tickers = tuple(ticker) if ticker else portfolio_tickers()
    exit_code = _guarded(_run_analyze(tickers))
    raise typer.Exit(code=exit_code)


def _build_price_provider(
    settings: Settings, http: httpx.AsyncClient
) -> CompositePriceProvider:
    """Wire the price sources: Yahoo primary, brapi fallback (ADR 0013).

    The live quote and the year history each try Yahoo first and fall back to
    brapi. brapi's token is only used on the fallback path; the primary Yahoo
    quote needs none.
    """
    brapi = BrapiPriceProvider(
        settings.brapi_base_url, settings.brapi_token.get_secret_value(), http
    )
    return CompositePriceProvider(
        quote=FallbackQuoteProvider(
            primary=YahooQuoteProvider(settings.yahoo_base_url, http),
            fallback=brapi,
        ),
        history=FallbackPriceHistory(
            primary=YahooPriceHistory(settings.yahoo_base_url, http),
            fallback=brapi,
        ),
    )


async def _run_analyze(tickers: tuple[str, ...]) -> int:
    settings = get_settings()
    mongo = await init_database(settings)
    engine = create_engine(settings)
    session_factory = create_session_factory(engine)
    try:
        async with httpx.AsyncClient(timeout=30.0) as http:
            use_case = AnalyzePortfolioUseCase(
                reader=MongoFundamentalsReader(
                    mongo[settings.mongo_db]["raw_ingestions"]
                ),
                price_provider=_build_price_provider(settings, http),
                repository=SqlAlchemyAnalysisRepository(session_factory),
                shares_reader=MongoSharesReader(
                    mongo[settings.mongo_db]["raw_ingestions"]
                ),
            )
            analyses = await use_case.execute(tickers)
    finally:
        await mongo.close()
        await engine.dispose()

    print(format_analysis(analyses))
    return 0


@app.command()
def doctor(
    ticker: list[str] | None = typer.Option(
        None, "--ticker", "-t", help="Ticker to inspect (repeatable). Default: all."
    ),
) -> None:
    """Coverage report over the persisted analysis — the M0 gate (read-only).

    Reads Postgres and reports, per ticker/view/exercise, the status of every
    indicator: a value, a null with a named cause, or an unclassified null. It
    never recomputes or persists.
    """
    tickers = tuple(ticker) if ticker else portfolio_tickers()
    exit_code = _guarded(_run_doctor(tickers))
    raise typer.Exit(code=exit_code)


async def _run_doctor(tickers: tuple[str, ...]) -> int:
    settings = get_settings()
    engine = create_engine(settings)
    session_factory = create_session_factory(engine)
    try:
        use_case = DoctorUseCase(SqlAlchemyAnalysisRepository(session_factory))
        report = await use_case.execute(tickers)
    finally:
        await engine.dispose()

    print(format_doctor(report))
    return 0


def _format_collection_log(outcomes: list[FetchOutcome]) -> str:
    """Human-readable collection log (plan §5.1)."""
    counts: dict[OutcomeStatus, int] = {}
    for outcome in outcomes:
        counts[outcome.status] = counts.get(outcome.status, 0) + 1

    lines = ["", "=== Collection log ==="]
    for outcome in outcomes:
        http = outcome.http_status if outcome.http_status is not None else "-"
        lines.append(
            f"  {outcome.ticker:<7} {outcome.module:<32} "
            f"{outcome.status.value:<8} HTTP {http}"
        )
    summary = ", ".join(f"{status.value}={n}" for status, n in sorted(counts.items()))
    lines.append(f"--- {len(outcomes)} calls | {summary or 'nothing collected'}")
    return "\n".join(lines)


def format_report(report: CompletenessReport) -> str:
    """Render the completeness report as readable text (plan §6)."""
    lines: list[str] = ["", "=== Completeness report ==="]
    for ticker_report in report.tickers:
        lines.extend(_format_ticker(ticker_report, report.depth_label))
    return "\n".join(lines)


def _format_ticker(ticker_report: TickerReport, depth_label: str) -> list[str]:
    collected = (
        ticker_report.last_collected_at.isoformat()
        if ticker_report.last_collected_at is not None
        else "never"
    )
    lines = [
        "",
        f"{ticker_report.ticker} [{ticker_report.sector.value}] "
        f"— max {depth_label}: {ticker_report.max_quarters} — collected: {collected}",
    ]
    for module in ticker_report.modules:
        mark = "OK " if module.present else "-- "
        lines.append(f"  {mark} {module.module:<32} {depth_label}={module.quarters}")
    check = ticker_report.sector_check
    present = ", ".join(check.present_fields) or "(none)"
    missing = ", ".join(check.missing_fields) or "(none)"
    lines.append(f"  sector signals present: {present}")
    lines.append(f"  sector signals MISSING: {missing}")
    return lines


def _num(value: Decimal | None) -> str:
    return "n/a" if value is None else f"{value:.2f}"


def _pct(value: Decimal | None) -> str:
    return "n/a" if value is None else f"{value * 100:.1f}%"


def format_analysis(analyses: list[TickerAnalysis]) -> str:
    """Render the computed indicators as readable text."""
    lines: list[str] = ["", "=== Analysis ==="]
    for a in analyses:
        i = a.indicators
        basis = (
            f" ({a.price_basis}, nominal {_num(a.price_nominal)})"
            if a.price_basis is not None
            else ""
        )
        lines.append(
            f"\n{a.ticker} [{a.sector.value}] {a.view} — ref {a.reference_date} "
            f"— price {_num(a.price)}{basis}"
        )
        lines.append(
            f"  ROE {_pct(i.roe)}  ROA {_pct(i.roa)}  net margin {_pct(i.net_margin)}"
            f"  gross {_pct(i.gross_margin)}  EBITDA mgn {_pct(i.ebitda_margin)}"
        )
        lines.append(
            f"  P/L {_num(i.pe)}  P/VP {_num(i.pb)}  EV/EBITDA {_num(i.ev_ebitda)}"
            f"  DY {_pct(i.dividend_yield)}"
        )
        lines.append(
            f"  net debt/EBITDA {_num(i.net_debt_to_ebitda)}"
            f"  current {_num(i.current_ratio)}"
            f"  rev growth {_pct(i.revenue_growth)}"
            f"  NI growth {_pct(i.net_income_growth)}"
        )
    return "\n".join(lines)


def _format_exercise(exercise: ExerciseCoverage) -> list[str]:
    """One header line per exercise, then a line per null cell with its cause."""
    total = len(exercise.indicators)
    header = (
        f"  {exercise.view:<11} ref {exercise.reference_date} "
        f"| {exercise.values}/{total} values, "
        f"named {exercise.named_nulls}, unclassified {exercise.unclassified}"
    )
    lines = [header]
    for cell in exercise.indicators:
        if cell.has_value:
            continue
        mark = "!!" if cell.is_unclassified else "  "
        lines.append(f"    {mark} {cell.indicator:<26} {cell.status}")
    return lines


def format_doctor(report: DoctorReport) -> str:
    """Render the coverage report and a status tally over every cell (#47)."""
    lines: list[str] = ["", "=== smaug doctor — persisted analysis coverage ==="]
    named: dict[NullReason, int] = {}
    values = unclassified = cells = exercises = 0
    tickers_with_unclassified: set[str] = set()

    for ticker_cov in report.tickers:
        lines.append(f"\n{ticker_cov.ticker} [{ticker_cov.sector.value}]")
        if not ticker_cov.exercises:
            lines.append("  !! (no persisted analysis)")
            continue
        for exercise in ticker_cov.exercises:
            exercises += 1
            lines.extend(_format_exercise(exercise))
            for cell in exercise.indicators:
                cells += 1
                if cell.has_value:
                    values += 1
                elif cell.reason is not None:
                    named[cell.reason] = named.get(cell.reason, 0) + 1
                else:
                    unclassified += 1
                    tickers_with_unclassified.add(ticker_cov.ticker)

    lines.append("")
    lines.append(
        f"--- {len(report.tickers)} tickers, {exercises} exercises, {cells} cells "
        f"| value={values} named={sum(named.values())} unclassified={unclassified}"
    )
    if named:
        breakdown = ", ".join(
            f"{reason.value}={n}" for reason, n in sorted(named.items())
        )
        lines.append(f"    named breakdown: {breakdown}")
    if unclassified:
        who = ", ".join(sorted(tickers_with_unclassified))
        lines.append(f"    !! {unclassified} unclassified nulls across: {who}")
    return "\n".join(lines)


def main() -> None:
    app()


if __name__ == "__main__":
    main()
