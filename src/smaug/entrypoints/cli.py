"""CLI entrypoints — the composition root for Phase 1.

Wires config -> Mongo -> brapi client -> repository -> use cases, and renders
results to stdout. No business logic lives here: the commands only assemble
dependencies and call the use cases (plan §3.1 / CLAUDE.md).
"""

from __future__ import annotations

import asyncio

import httpx
import typer

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
from smaug.ingestion.infrastructure.brapi_client import BrapiClient
from smaug.ingestion.infrastructure.repositories import BeanieRawIngestionRepository
from smaug.portfolio.domain.sectors import portfolio_tickers
from smaug.shared.config import get_settings
from smaug.shared.db import init_database
from smaug.shared.events import EventBus
from smaug.shared.logging import get_logger

app = typer.Typer(help="smaug — faithful brapi ingestion (Phase 1).")
logger = get_logger("smaug.cli")

_FAILED_STATUSES = frozenset({OutcomeStatus.ERROR, OutcomeStatus.ABORTED})


@app.command()
def ingest(
    ticker: list[str] | None = typer.Option(
        None, "--ticker", "-t", help="Ticker to collect (repeatable). Default: all."
    ),
) -> None:
    """Collect the configured brapi modules and store the raw mirror."""
    tickers = tuple(ticker) if ticker else portfolio_tickers()
    exit_code = asyncio.run(_run_ingest(tickers))
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


async def _run_ingest(tickers: tuple[str, ...]) -> int:
    settings = get_settings()
    token = settings.require_token()
    client = await init_database(settings)
    try:
        async with httpx.AsyncClient(timeout=30.0) as http:
            use_case = IngestPortfolioUseCase(
                client=BrapiClient(settings.brapi_base_url, token, http),
                repository=BeanieRawIngestionRepository(),
                event_bus=EventBus(),
                modules=settings.brapi_modules,
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
            modules=settings.brapi_modules,
        )
        completeness = await use_case.execute(tickers)
    finally:
        await client.close()

    print(format_report(completeness))


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
        lines.extend(_format_ticker(ticker_report))
    return "\n".join(lines)


def _format_ticker(ticker_report: TickerReport) -> list[str]:
    collected = (
        ticker_report.last_collected_at.isoformat()
        if ticker_report.last_collected_at is not None
        else "never"
    )
    lines = [
        "",
        f"{ticker_report.ticker} [{ticker_report.sector.value}] "
        f"— max quarters: {ticker_report.max_quarters} — collected: {collected}",
    ]
    for module in ticker_report.modules:
        mark = "OK " if module.present else "-- "
        lines.append(f"  {mark} {module.module:<32} quarters={module.quarters}")
    check = ticker_report.sector_check
    present = ", ".join(check.present_fields) or "(none)"
    missing = ", ".join(check.missing_fields) or "(none)"
    lines.append(f"  sector fields present: {present}")
    lines.append(f"  sector fields MISSING: {missing}")
    return lines


def main() -> None:
    app()


if __name__ == "__main__":
    main()
