"""CLI entrypoints — the composition root for Phase 1.

Wires config -> Mongo -> brapi client -> repository -> use cases, and renders
results to stdout. No business logic lives here: the commands only assemble
dependencies and call the use cases (plan §3.1 / CLAUDE.md).
"""

from __future__ import annotations

import asyncio
from decimal import Decimal
from typing import cast

import httpx
import typer

from smaug.analysis.application.analyze import AnalyzePortfolioUseCase
from smaug.analysis.domain.entities import TickerAnalysis
from smaug.analysis.infrastructure.brapi_price import BrapiPriceProvider
from smaug.analysis.infrastructure.mongo_capital import MongoSharesReader
from smaug.analysis.infrastructure.mongo_fundamentals import MongoFundamentalsReader
from smaug.analysis.infrastructure.sql_repository import SqlAlchemyAnalysisRepository
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
from smaug.shared.events import EventBus
from smaug.shared.logging import get_logger
from smaug.shared.sql_db import create_engine, create_session_factory

app = typer.Typer(help="smaug — CVM/brapi ingestion and indicator analysis.")
logger = get_logger("smaug.cli")

_FAILED_STATUSES = frozenset({OutcomeStatus.ERROR, OutcomeStatus.ABORTED})


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
    asyncio.run(_run_report(tickers))


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
    exit_code = asyncio.run(_run_analyze(tickers))
    raise typer.Exit(code=exit_code)


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
                price_provider=BrapiPriceProvider(
                    settings.brapi_base_url,
                    settings.brapi_token.get_secret_value(),
                    http,
                ),
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


def main() -> None:
    app()


if __name__ == "__main__":
    main()
