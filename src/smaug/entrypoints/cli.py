"""CLI entrypoints — the composition root for Phase 1.

Wires config -> Mongo -> source readers -> repository -> use cases, and renders
results to stdout. No business logic lives here: the commands only assemble
dependencies and call the use cases (plan §3.1 / CLAUDE.md).
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable, Coroutine, Sequence
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import Any, cast

import httpx
import typer

from smaug.analysis.application.analyze import (
    AnalysisRun,
    AnalysisStatus,
    AnalyzePortfolioUseCase,
)
from smaug.analysis.application.doctor import (
    DoctorReport,
    DoctorUseCase,
    ExerciseCoverage,
)
from smaug.analysis.application.drift import AccountDriftUseCase, DriftReport
from smaug.analysis.domain.entities import TickerAnalysis
from smaug.analysis.domain.indicators import NullReason
from smaug.analysis.domain.ports import (
    CashEventReader,
    PriceProvider,
    SharesReader,
)
from smaug.analysis.infrastructure.b3_prices import (
    B3BaseChanges,
    B3PriceProvider,
    CotahistArchive,
)
from smaug.analysis.infrastructure.dividend_adjusted_price import (
    DividendAdjustedPriceProvider,
)
from smaug.analysis.infrastructure.mongo_capital import MongoSharesReader
from smaug.analysis.infrastructure.mongo_dividends import MongoCashEventReader
from smaug.analysis.infrastructure.mongo_fundamentals import MongoFundamentalsReader
from smaug.analysis.infrastructure.restated_price import RestatedPriceProvider
from smaug.analysis.infrastructure.sql_repository import SqlAlchemyAnalysisRepository
from smaug.analysis.infrastructure.succession import (
    CodeSuccession,
    SuccessionPriceProvider,
)
from smaug.ingestion.application.ingest import (
    FetchOutcome,
    IngestPortfolioUseCase,
    OutcomeStatus,
)
from smaug.ingestion.application.relink import RelinkMirrorUseCase, RelinkReport
from smaug.ingestion.application.report import (
    CompletenessReport,
    CompletenessReportUseCase,
    TickerReport,
)
from smaug.ingestion.domain.repositories import RawIngestionRepository
from smaug.ingestion.infrastructure.b3_capital_events import (
    CAPITAL_EVENT_B3_MODULE,
    B3CapitalEventSource,
)
from smaug.ingestion.infrastructure.b3_cash_dividends import (
    CASH_DIVIDEND_B3_MODULE,
    B3CashDividendSource,
)
from smaug.ingestion.infrastructure.cvm_capital import (
    CAPITAL_EVENT_MODULE,
    CAPITAL_MODULE,
    TREASURY_MODULE,
    CvmCapitalEventSource,
    CvmCapitalSource,
    CvmTreasurySource,
)
from smaug.ingestion.infrastructure.cvm_source import CvmDataSource, CvmDocument
from smaug.ingestion.infrastructure.repositories import BeanieRawIngestionRepository
from smaug.ingestion.infrastructure.routed_source import RoutedDataSource
from smaug.portfolio.application.refresh_taxonomy import (
    RefreshTaxonomyUseCase,
    TaxonomyDrift,
)
from smaug.portfolio.domain.company import CompanyIdentity
from smaug.portfolio.domain.cvm_codes import TICKER_TO_CNPJ, TICKER_TO_CVM_CODE
from smaug.portfolio.domain.listings import listed_since
from smaug.portfolio.domain.sectors import (
    PORTFOLIO,
    Sector,
    portfolio_tickers,
    sector_from_cvm,
)
from smaug.portfolio.domain.securities import SiblingCodesResolver
from smaug.portfolio.domain.share_classes import ShareClass, listed_classes
from smaug.portfolio.domain.taxonomy import (
    TAXONOMY_SNAPSHOT,
    Classification,
    classify,
)
from smaug.portfolio.domain.universe import ListedCompany
from smaug.portfolio.infrastructure.b3_taxonomy import B3TaxonomySource
from smaug.portfolio.infrastructure.cvm_registry import CvmCompanyRegistry
from smaug.portfolio.infrastructure.cvm_securities import CvmSecurityHistory
from smaug.shared.config import Settings, get_settings
from smaug.shared.db import init_database
from smaug.shared.errors import UnknownTickerError
from smaug.shared.events import EventBus
from smaug.shared.logging import get_logger
from smaug.shared.sql_db import create_engine, create_session_factory

app = typer.Typer(help="smaug — CVM/B3 ingestion and indicator analysis.")
logger = get_logger("smaug.cli")

_FAILED_STATUSES = frozenset({OutcomeStatus.ERROR, OutcomeStatus.ABORTED})


@dataclass(frozen=True)
class YearPass:
    """What one archive year of a collection run produced."""

    year: int
    outcomes: list[FetchOutcome]
    companies: int
    already_mirrored: int


def _guarded[T](coro: Coroutine[Any, Any, T]) -> T:
    """Run a use-case coroutine, turning an unknown ticker into a clean exit.

    Keeps the raw ``KeyError`` from ``sector_of`` off the terminal — the CLI
    reports a typo (or a not-yet-added ticker) as one line, like the ingestion
    side maps a source's HTTP errors to typed ones.
    """
    try:
        return asyncio.run(coro)
    except UnknownTickerError as exc:
        typer.echo(f"error: {exc}", err=True)
        raise typer.Exit(code=2) from exc


async def _registry_identities(
    settings: Settings, http: httpx.AsyncClient, tickers: tuple[str, ...]
) -> dict[str, CompanyIdentity]:
    """Resolve tickers outside the curated nine via the CVM FCA registry.

    The nine keep their verified ``cvm_codes.py`` keys and never trigger an FCA
    download; any other requested ticker is resolved on demand. A ticker that
    resolves nowhere is a user error — a typo, or a company CVM does not list —
    and raises ``UnknownTickerError``, the same clean exit the curated guard gave
    (this replaces ``require_portfolio_tickers``).
    """
    unknown = [t for t in tickers if t not in PORTFOLIO]
    if not unknown:
        return {}
    registry = CvmCompanyRegistry(
        http, year=settings.cvm_year, cache_dir=settings.cvm_cache_dir
    )
    identities = await registry.resolve_all(unknown)
    for ticker in unknown:
        if ticker not in identities:
            raise UnknownTickerError(ticker)
    return identities


def _sector_resolver(
    identities: dict[str, CompanyIdentity],
) -> Callable[[str], Sector]:
    """A ``Sector`` for any requested ticker: curated for the nine, else the CVM
    activity label folded to the enum (``sector_from_cvm``)."""

    def resolve(ticker: str) -> Sector:
        if ticker in PORTFOLIO:
            return PORTFOLIO[ticker]
        identity = identities.get(ticker)
        if identity is None:
            raise UnknownTickerError(ticker)
        return sector_from_cvm(identity.cvm_sector)

    return resolve


def _registrant_resolver(
    identities: dict[str, CompanyIdentity],
) -> Callable[[str], str | None]:
    """The registrant whose filings a ticker reads (``CD_CVM``, ADR 0030).

    Curated for the nine, registry-resolved for the rest — the same two-step every
    other resolver here takes, and for the same reason: the nine never trigger an
    FCA download.
    """

    def resolve(ticker: str) -> str | None:
        curated = TICKER_TO_CVM_CODE.get(ticker)
        if curated is not None:
            return curated
        identity = identities.get(ticker)
        return identity.cd_cvm if identity is not None else None

    return resolve


def _classification_resolver(
    identities: dict[str, CompanyIdentity],
) -> Callable[[str], Classification]:
    """The B3 ``Classification`` for a ticker: snapshot, else the CVM fallback."""

    def resolve(ticker: str) -> Classification:
        identity = identities.get(ticker)
        cvm_sector = identity.cvm_sector if identity is not None else None
        classification = classify(ticker, cvm_sector)
        if classification is None:
            raise UnknownTickerError(ticker)
        return classification

    return resolve


def _listed_since_resolver(
    identities: dict[str, CompanyIdentity],
) -> Callable[[str], date | None]:
    """When a ticker was listed: curated for the nine, else the FCA (#153).

    Curated first for the same reason the classes are — the nine never trigger an
    FCA download, so the registry holds nothing for them.
    """

    def resolve(ticker: str) -> date | None:
        curated = listed_since(ticker)
        if curated is not None:
            return curated
        identity = identities.get(ticker)
        return identity.listed_since if identity is not None else None

    return resolve


def _classes_resolver(
    identities: dict[str, CompanyIdentity],
) -> Callable[[str], tuple[ShareClass, ...]]:
    """The listed ON/PN classes for the cap: curated for the nine, else FCA."""

    def resolve(ticker: str) -> tuple[ShareClass, ...]:
        curated = listed_classes(ticker)
        if curated:
            return curated
        identity = identities.get(ticker)
        return identity.share_classes if identity is not None else ()

    return resolve


async def _cvm_key_maps(
    settings: Settings, http: httpx.AsyncClient, tickers: tuple[str, ...]
) -> tuple[dict[str, str], dict[str, str]]:
    """The ticker -> CD_CVM and ticker -> CNPJ maps the CVM sources need.

    Curated for the nine (verified, offline), registry-resolved for the rest.
    """
    code = {t: TICKER_TO_CVM_CODE[t] for t in tickers if t in TICKER_TO_CVM_CODE}
    cnpj = {t: TICKER_TO_CNPJ[t] for t in tickers if t in TICKER_TO_CNPJ}
    identities = await _registry_identities(settings, http, tickers)
    for ticker, identity in identities.items():
        code[ticker] = identity.cd_cvm
        cnpj[ticker] = identity.cnpj
    return code, cnpj


@app.command()
def ingest(
    ticker: list[str] | None = typer.Option(
        None, "--ticker", "-t", help="Ticker to collect (repeatable). Default: all."
    ),
    all_listed: bool = typer.Option(
        False, "--all", "-a", help="Every listed company, from the CVM FCA registry."
    ),
    document: str | None = typer.Option(
        None, "--document", help="CVM document: ITR or DFP (overrides config)."
    ),
    year: int | None = typer.Option(
        None, "--year", help="CVM file year to mirror (overrides config)."
    ),
    from_year: int | None = typer.Option(
        None, "--from-year", help="First year of a range to sweep (with --to-year)."
    ),
    to_year: int | None = typer.Option(
        None, "--to-year", help="Last year of a range to sweep (with --from-year)."
    ),
    force: bool = typer.Option(
        False,
        "--force",
        help="Re-collect a company already mirrored from that year's archive.",
    ),
    verbose: bool = typer.Option(
        False, "--verbose", "-v", help="Log every call instead of a per-year summary."
    ),
) -> None:
    """Collect the configured modules for the active source and store the mirror.

    Three scopes: the curated nine (default), an explicit ``--ticker`` list, or
    ``--all`` — every company the CVM registry lists, which is what M2 means by
    running at exchange scale. A run over 368 companies and eleven years is one
    command, because each year's archive is read once and served to every company
    in it (``--from-year``/``--to-year``).
    """
    if all_listed and ticker:
        raise typer.BadParameter("--all and --ticker are mutually exclusive")
    years = _years_to_sweep(year, from_year, to_year)
    tickers = () if all_listed else (tuple(ticker) if ticker else portfolio_tickers())
    try:
        # _guarded turns an unknown ticker into a clean exit, like analyze (#13).
        exit_code = _guarded(
            _run_ingest(
                tickers,
                document=document,
                years=years,
                whole_exchange=all_listed,
                force=force,
                verbose=verbose,
            )
        )
    except NotImplementedError as exc:
        typer.echo(f"error: {exc}", err=True)
        raise typer.Exit(code=2) from exc
    raise typer.Exit(code=exit_code)


def _years_to_sweep(
    year: int | None, from_year: int | None, to_year: int | None
) -> tuple[int | None, ...]:
    """The years one run covers: a range, a single year, or the configured one.

    ``(None,)`` means "whatever the config says" — the shape the single-year path
    already had, kept so ``--year`` and no flag at all behave exactly as before.
    """
    if from_year is None and to_year is None:
        return (year,)
    if year is not None:
        raise typer.BadParameter("--year cannot be combined with --from-year/--to-year")
    if from_year is None or to_year is None:
        raise typer.BadParameter("--from-year and --to-year go together")
    if from_year > to_year:
        raise typer.BadParameter("--from-year must not be after --to-year")
    return tuple(range(from_year, to_year + 1))


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
    ticker_to_code: dict[str, str],
    ticker_to_cnpj: dict[str, str],
    *,
    document: str | None = None,
    year: int | None = None,
) -> RoutedDataSource:
    """Build the raw source: CVM's archives, with B3's endpoints routed per module.

    ``document``/``year`` override the config for one run (e.g. to pull several
    CVM files). The CVM key maps are resolved upstream (curated nine + FCA
    registry).
    """
    doc = (document or settings.cvm_document).upper()
    if doc not in ("ITR", "DFP"):
        raise typer.BadParameter("--document must be ITR or DFP")
    cvm_year = year or settings.cvm_year
    statements = CvmDataSource(
        http,
        ticker_to_code,
        year=cvm_year,
        cache_dir=settings.cvm_cache_dir,
        document=cast(CvmDocument, doc),
    )
    # The share counts live in a different CVM archive (FRE), keyed by CNPJ.
    capital = CvmCapitalSource(
        http,
        ticker_to_cnpj,
        year=cvm_year,
        cache_dir=settings.cvm_cache_dir,
        ticker_to_code=ticker_to_code,
    )
    # ...and the statements ZIP has a composition of its own, which is the only
    # place treasury shares are filed. Also keyed by CNPJ, not by CD_CVM.
    treasury = CvmTreasurySource(
        http,
        ticker_to_cnpj,
        year=cvm_year,
        cache_dir=settings.cvm_cache_dir,
        ticker_to_code=ticker_to_code,
        document=cast(CvmDocument, doc),
    )
    # The same FRE ZIP also declares the corporate actions outright, which the
    # share counts alone can only be guessed at (ADR 0027 guesses, and conflates
    # a split with the issuance that follows it).
    events = CvmCapitalEventSource(
        http,
        ticker_to_cnpj,
        year=cvm_year,
        cache_dir=settings.cvm_cache_dir,
        ticker_to_code=ticker_to_code,
    )
    # ...and B3 declares the same events without the counts, but *with* the last
    # session quoted on the old base — the two are complementary, and neither
    # covers the other's years (ADR 0034).
    exchange_events = B3CapitalEventSource(
        http,
        ticker_to_code=ticker_to_code,
        base_url=settings.b3_listed_base_url,
    )
    # ...and the cash it paid out, which no price file carries and which the
    # third price basis is rebuilt from (ADR 0039).
    cash_dividends = B3CashDividendSource(
        http,
        ticker_to_code=ticker_to_code,
        base_url=settings.b3_listed_base_url,
    )
    return RoutedDataSource(
        {
            CAPITAL_MODULE: capital,
            TREASURY_MODULE: treasury,
            CAPITAL_EVENT_MODULE: events,
            CAPITAL_EVENT_B3_MODULE: exchange_events,
            CASH_DIVIDEND_B3_MODULE: cash_dividends,
        },
        default=statements,
    )


async def _run_ingest(
    tickers: tuple[str, ...],
    *,
    document: str | None = None,
    years: tuple[int | None, ...] = (None,),
    whole_exchange: bool = False,
    force: bool = False,
    verbose: bool = False,
) -> int:
    settings = get_settings()
    client = await init_database(settings)
    repository = BeanieRawIngestionRepository()
    passes: list[YearPass] = []
    try:
        async with httpx.AsyncClient(timeout=30.0) as http:
            companies = await _universe(settings, http) if whole_exchange else ()
            for year in years:
                passes.append(
                    await _ingest_one_year(
                        settings,
                        http,
                        repository,
                        tickers,
                        companies,
                        document=document,
                        year=year,
                        whole_exchange=whole_exchange,
                        force=force,
                    )
                )
    finally:
        await client.close()

    outcomes = [outcome for pass_ in passes for outcome in pass_.outcomes]
    print(_format_collection_log(outcomes) if verbose else format_batch_log(passes))
    return 1 if any(o.status in _FAILED_STATUSES for o in outcomes) else 0


async def _universe(
    settings: Settings, http: httpx.AsyncClient
) -> tuple[ListedCompany, ...]:
    """Every listed company — the iteration unit of a whole-exchange run (#109)."""
    registry = CvmCompanyRegistry(
        http, year=settings.cvm_year, cache_dir=settings.cvm_cache_dir
    )
    companies = await registry.companies()
    logger.info("Universe: %d listed companies", len(companies))
    return companies


async def _ingest_one_year(
    settings: Settings,
    http: httpx.AsyncClient,
    repository: BeanieRawIngestionRepository,
    tickers: tuple[str, ...],
    companies: tuple[ListedCompany, ...],
    *,
    document: str | None,
    year: int | None,
    whole_exchange: bool,
    force: bool,
) -> YearPass:
    """Collect one CVM archive year (or the single configured pass).

    The archive is downloaded and indexed once here and then serves every company
    in it, which is why a year costs seconds of CPU rather than a request each.
    """
    # Resolve the registrant keys up front: an unknown ticker is a user error
    # rejected before any statement download (#60), not a 404-skip.
    if whole_exchange:
        code_map = {c.ticker: c.cd_cvm for c in companies}
        cnpj_map = {c.ticker: c.cnpj for c in companies}
        wanted = tuple(c.ticker for c in companies)
    else:
        code_map, cnpj_map = await _cvm_key_maps(settings, http, tickers)
        wanted = tickers

    source = _build_data_source(
        settings, http, code_map, cnpj_map, document=document, year=year
    )
    modules = settings.cvm_modules
    plan = dict.fromkeys(wanted, modules)
    if whole_exchange and not force:
        plan = await _work_plan(repository, source, wanted, code_map, modules)

    outcomes: list[FetchOutcome] = []
    for owed, tickers in _by_owed_modules(plan):
        use_case = IngestPortfolioUseCase(
            client=source,
            repository=repository,
            event_bus=EventBus(),
            modules=owed,
        )
        outcomes.extend(await use_case.execute(tickers))
        if any(o.status is OutcomeStatus.ABORTED for o in outcomes):
            # The use case stops the run on a fatal error; the next group would
            # meet the same one (a dead ZIP is dead for every module).
            break
    return YearPass(
        year=year if year is not None else settings.cvm_year,
        outcomes=outcomes,
        companies=len(plan),
        already_mirrored=len(wanted) - len(plan),
    )


async def _work_plan(
    repository: RawIngestionRepository,
    source: RoutedDataSource,
    wanted: tuple[str, ...],
    code_map: dict[str, str],
    modules: Sequence[str],
) -> dict[str, tuple[str, ...]]:
    """What each company is still owed, module by module — the resume guard.

    A whole-exchange run is long enough to be interrupted, and the mirror is
    append-only: without this, resuming would file a second identical copy of
    every company already collected. The unit is the *call*, not the company
    (#178): asking only "has this registrant been collected?" answered yes for a
    module added to the config afterwards, which it had never been asked for, and
    the run reported success having stored nothing. ``--force`` collects
    regardless, which is what a re-run after an amended archive wants.
    """
    done = {
        module: await repository.mirrored_for(module, file=source.archive_for(module))
        for module in modules
    }
    plan: dict[str, tuple[str, ...]] = {}
    for ticker in wanted:
        code = code_map.get(ticker)
        owed = tuple(m for m in modules if code is None or code not in done[m])
        if owed:
            plan[ticker] = owed
    return plan


def _by_owed_modules(
    plan: dict[str, tuple[str, ...]],
) -> list[tuple[tuple[str, ...], tuple[str, ...]]]:
    """Group the plan by the set of modules owed, so each set is one pass.

    A resumed run splits in two: the companies never collected owe everything,
    the ones already mirrored owe only the module that was added since. A fresh
    sweep has a single group and behaves exactly as it did before.
    """
    groups: dict[tuple[str, ...], list[str]] = {}
    for ticker, owed in plan.items():
        groups.setdefault(owed, []).append(ticker)
    return [(owed, tuple(tickers)) for owed, tickers in groups.items()]


async def _run_report(tickers: tuple[str, ...]) -> None:
    settings = get_settings()
    client = await init_database(settings)
    try:
        async with httpx.AsyncClient(timeout=30.0) as http:
            identities = await _registry_identities(settings, http, tickers)
        use_case = CompletenessReportUseCase(
            repository=BeanieRawIngestionRepository(),
            modules=settings.cvm_modules,
            sector_resolver=_sector_resolver(identities),
            registrant_resolver=_registrant_resolver(identities),
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
    all_listed: bool = typer.Option(
        False, "--all", "-a", help="Every traded code the CVM registry lists."
    ),
    verbose: bool = typer.Option(
        False, "--verbose", "-v", help="Print every indicator instead of a summary."
    ),
) -> None:
    """Compute the fundamental + market indicators and store them in Postgres.

    ``--all`` analyses every traded code rather than every company: a company's
    classes share one filing but not one price, so ELET3 and ELET6 are two
    different answers to "what is this worth". It runs sequentially — measured at
    about six seconds a code, with no sign of the price sources rate-limiting.
    """
    if all_listed and ticker:
        raise typer.BadParameter("--all and --ticker are mutually exclusive")
    tickers = () if all_listed else (tuple(ticker) if ticker else portfolio_tickers())
    exit_code = _guarded(
        _run_analyze(tickers, whole_exchange=all_listed, verbose=verbose)
    )
    raise typer.Exit(code=exit_code)


async def _siblings_resolver(
    settings: Settings, http: httpx.AsyncClient
) -> SiblingCodesResolver:
    """The other codes each share class has been filed under (#193).

    Read across every FCA year that carries the trading code, which is 2018 on —
    a rename older than that column is not in the archive at all, and the price
    side reports the years it cannot name rather than serving part of them.
    """
    history = CvmSecurityHistory(
        http,
        # Through today rather than the mirrored year: a code renamed this year
        # is named by no earlier archive, and the running year's file is skipped
        # if CVM has not published it yet.
        through=max(settings.cvm_year, date.today().year),
        cache_dir=settings.cvm_cache_dir,
    )
    return await history.resolver()


def _build_archive(settings: Settings, http: httpx.AsyncClient) -> CotahistArchive:
    """B3's quote series — the only thing that prices the analysis (ADR 0041).

    One instance for the whole run: the share side reads the corporate-action
    dates off it (ADR 0035) and the price side reads the closes, and they must
    not each stream the same gigabytes.
    """
    return CotahistArchive(
        http,
        cache_dir=settings.b3_cache_dir,
        base_url=settings.b3_series_base_url,
    )


def _build_price_provider(
    shares_reader: SharesReader,
    archive: CotahistArchive,
    cash_events: CashEventReader,
    succession: CodeSuccession,
) -> PriceProvider:
    """Wire the exchange's series into the two bases derived from it.

    One file answers both the live quote and the year history, so there is no
    chain here and no fallback: a company B3 does not list reads as a missing
    price rather than as somebody else's number on another basis (ADR 0041).

    The succession is innermost because it decides *which sessions exist* (ADR
    0042): a security that changed trading code has its earlier years filed under
    the earlier code, and both bases above have to be derived from the joined
    series rather than from the tail of it. It reads the share history too, and
    for a different question than the restatement outside it: whether a seam the
    price does not carry across is a share-base move already dated, and so one
    the sessions before it are restated by (ADR 0043).

    The share history wraps it because the exchange publishes what printed on the
    tape while the counts it multiplies are restated onto today's base (ADR 0027)
    — the two have to meet on one base or every company that ever split is
    mispriced. Dividends first, restatement outermost: the third basis is the
    second one with the cash put back, so both end up on the same share base
    (ADR 0039).
    """
    return RestatedPriceProvider(
        DividendAdjustedPriceProvider(
            SuccessionPriceProvider(
                B3PriceProvider(archive),
                succession,
                timeline=shares_reader.restatement_timeline,
            ),
            cash_events,
        ),
        shares_reader,
    )


async def _run_analyze(
    tickers: tuple[str, ...], *, whole_exchange: bool = False, verbose: bool = False
) -> int:
    settings = get_settings()
    mongo = await init_database(settings)
    engine = create_engine(settings)
    session_factory = create_session_factory(engine)
    try:
        async with httpx.AsyncClient(timeout=30.0) as http:
            if whole_exchange:
                tickers, identities = await _universe_tickers(settings, http)
            else:
                identities = await _registry_identities(settings, http, tickers)
            # The reader keeps a five-value Sector (the internal regime hint); the
            # stored analysis carries the B3 Classification (ADR 0024). Both are
            # built from the same resolved identities.
            registrant = _registrant_resolver(identities)
            # One reader, wired twice: the use case divides the per-share
            # indicators by its restated counts, and the price provider divides
            # the as-traded price by the very same restatement (ADR 0027). The
            # archive is built first because the reader dates that restatement
            # off it, and the price provider then shares the same instance.
            archive = _build_archive(settings, http)
            # One chain of trading codes per security, shared by the two readers
            # that must not disagree about it: the price averages the joined
            # sessions and the base-change reader dates the actions filed under
            # the codes those sessions came from (ADR 0042).
            succession = CodeSuccession(
                archive,
                siblings=await _siblings_resolver(settings, http),
                listed_since=_listed_since_resolver(identities),
            )
            shares_reader = MongoSharesReader(
                mongo[settings.mongo_db]["raw_ingestions"],
                registrant_resolver=registrant,
                # The *candidates*, not the joined chain: a seam the price will
                # refuse is still the session an action took effect on, and this
                # is the reader that dates it (ADR 0043).
                base_changes=B3BaseChanges(archive, codes=succession.candidates),
            )
            cash_events = MongoCashEventReader(
                mongo[settings.mongo_db]["raw_ingestions"],
                registrant_resolver=registrant,
            )
            use_case = AnalyzePortfolioUseCase(
                reader=MongoFundamentalsReader(
                    mongo[settings.mongo_db]["raw_ingestions"],
                    sector_resolver=_sector_resolver(identities),
                    registrant_resolver=registrant,
                ),
                price_provider=_build_price_provider(
                    shares_reader, archive, cash_events, succession
                ),
                repository=SqlAlchemyAnalysisRepository(session_factory),
                shares_reader=shares_reader,
                classification_resolver=_classification_resolver(identities),
                classes_resolver=_classes_resolver(identities),
                listed_since_resolver=_listed_since_resolver(identities),
            )
            run = await use_case.execute(tickers)
    finally:
        await mongo.close()
        await engine.dispose()

    print(format_analysis(run.analyses) if verbose else format_analysis_run(run))
    return 1 if run.failed else 0


async def _universe_tickers(
    settings: Settings, http: httpx.AsyncClient
) -> tuple[tuple[str, ...], dict[str, CompanyIdentity]]:
    """Every traded code, with the identity each resolver needs (#109).

    The unit here is the **ticker**, not the company the mirror is keyed on: a
    company's classes trade at different prices, so ELET3 and ELET6 have
    different multiples over the one filing they share. Analysing only the
    company's ON share would leave the other codes unanswerable — including to
    someone typing one into the search box.
    """
    registry = CvmCompanyRegistry(
        http, year=settings.cvm_year, cache_dir=settings.cvm_cache_dir
    )
    tickers = tuple(sorted(t for c in await registry.companies() for t in c.tickers))
    identities = await registry.resolve_all(tickers)
    logger.info("Universe: %d traded codes", len(tickers))
    return tickers, identities


@app.command()
def doctor(
    ticker: list[str] | None = typer.Option(
        None, "--ticker", "-t", help="Ticker to inspect (repeatable). Default: all."
    ),
    all_listed: bool = typer.Option(
        False, "--all", "-a", help="Every traded code the CVM registry lists."
    ),
    verbose: bool = typer.Option(
        False, "--verbose", "-v", help="Print every null cell instead of a summary."
    ),
) -> None:
    """Coverage report over the persisted analysis — the M0 gate (read-only).

    Reads Postgres and reports, per ticker/view/exercise, the status of every
    indicator: a value, a null with a named cause, or an unclassified null. It
    never recomputes or persists.

    Then a second section on a second axis: which mapped *accounts* changed
    status across a ticker's closed years (#156). Coverage alone cannot tell a
    stale needle from a line the filer never publishes; the transition can.

    Over the whole exchange the per-cell listing is tens of thousands of lines,
    so ``--all`` summarizes and ``--verbose`` restores the detail. What the
    summary keeps is the part that needs acting on: every ticker carrying a null
    nobody has named.
    """
    if all_listed and ticker:
        raise typer.BadParameter("--all and --ticker are mutually exclusive")
    tickers = () if all_listed else (tuple(ticker) if ticker else portfolio_tickers())
    exit_code = _guarded(
        _run_doctor(tickers, whole_exchange=all_listed, verbose=verbose)
    )
    raise typer.Exit(code=exit_code)


async def _run_doctor(
    tickers: tuple[str, ...], *, whole_exchange: bool = False, verbose: bool = False
) -> int:
    settings = get_settings()
    engine = create_engine(settings)
    session_factory = create_session_factory(engine)
    mongo = await init_database(settings)
    try:
        async with httpx.AsyncClient(timeout=30.0) as http:
            if whole_exchange:
                tickers, identities = await _universe_tickers(settings, http)
            else:
                identities = await _registry_identities(settings, http, tickers)
        resolver = _sector_resolver(identities)
        use_case = DoctorUseCase(
            SqlAlchemyAnalysisRepository(session_factory), sector_resolver=resolver
        )
        report = await use_case.execute(tickers)
        # The drift section reads the mirror, not Postgres: the standardized
        # accounts it compares are never persisted — only the indicators derived
        # from them are.
        drift = await AccountDriftUseCase(
            MongoFundamentalsReader(
                mongo[settings.mongo_db]["raw_ingestions"],
                sector_resolver=resolver,
                registrant_resolver=_registrant_resolver(identities),
            )
        ).execute(tickers)
    finally:
        await mongo.close()
        await engine.dispose()

    if verbose:
        print(format_doctor(report))
        print(format_drift(drift))
    else:
        print(format_doctor_summary(report))
        print(format_drift_summary(drift))
    return 0


@app.command()
def taxonomy(
    write: bool = typer.Option(
        False, "--write", help="Rewrite the committed snapshot with what B3 says now."
    ),
) -> None:
    """Report how the committed B3 taxonomy has drifted, or regenerate it (#112).

    B3 republishes the *Classificação Setorial* weekly, so the snapshot is stale
    by construction and the useful question is what moved. By default this only
    reports, exiting non-zero when anything did; ``--write`` records it — a
    deliberate act, because every stored analysis carries the classification it
    was computed under.
    """
    exit_code = _guarded(_run_taxonomy(write=write))
    raise typer.Exit(code=exit_code)


async def _run_taxonomy(*, write: bool) -> int:
    settings = get_settings()
    async with httpx.AsyncClient(timeout=30.0) as http:
        companies = await _universe(settings, http)
        fetched = await B3TaxonomySource(http, cache_dir=settings.cvm_cache_dir).fetch(
            companies
        )

    use_case = RefreshTaxonomyUseCase(TAXONOMY_SNAPSHOT)
    drift = use_case.drift(
        fetched.classifications,
        unclassified=fetched.unclassified,
        unknown_labels=fetched.unknown_labels,
        from_sheet=fetched.from_sheet,
        from_detail=fetched.from_detail,
    )
    print(format_taxonomy_drift(drift, companies=len(companies)))
    if write:
        count = use_case.write(fetched.classifications)
        print(f"\nWrote {count} ticker(s) to {TAXONOMY_SNAPSHOT.name}.")
        return 0
    return 1 if drift.moved else 0


@app.command()
def relink() -> None:
    """Name the registrant on CVM documents mirrored before the key moved (ADR 0030).

    The readers filter the mirror by ``CD_CVM`` so a company's share classes read
    one filing instead of a copy each. Documents collected before that carry only
    the ticker they were requested under; this stamps the company onto them, which
    is a relabelling — no download, no payload touched. Idempotent, and a
    deliberate maintenance action like ``prune``.
    """
    exit_code = _guarded(_run_relink())
    raise typer.Exit(code=exit_code)


async def _run_relink() -> int:
    settings = get_settings()
    client = await init_database(settings)
    repository = BeanieRawIngestionRepository()
    try:
        pending = await repository.unlinked_tickers()
        async with httpx.AsyncClient(timeout=30.0) as http:
            identities = await _registry_identities(settings, http, pending)
        report = await RelinkMirrorUseCase(
            repository, registrant_resolver=_registrant_resolver(identities)
        ).execute()
    finally:
        await client.close()

    print(format_relink(report))
    # An unresolved ticker is a gap, not a crash: its documents stay readable
    # under the old key, and the non-zero exit says the mirror is not fully keyed.
    return 1 if report.unresolved else 0


@app.command()
def prune() -> None:
    """Delete superseded analysis runs, keeping only the latest per cell (#71).

    ``ticker_analysis`` is append-only: every ``analyze`` inserts fresh rows and the
    reads already take the latest per (ticker, view, reference_date). This reclaims
    the space the older, shadowed runs hold. ``doctor`` and the API are unchanged —
    they ignore those rows anyway. A deliberate maintenance action, never a side
    effect of ``analyze``.
    """
    exit_code = _guarded(_run_prune())
    raise typer.Exit(code=exit_code)


async def _run_prune() -> int:
    settings = get_settings()
    engine = create_engine(settings)
    session_factory = create_session_factory(engine)
    try:
        result = await SqlAlchemyAnalysisRepository(session_factory).prune()
    finally:
        await engine.dispose()

    print(
        f"Pruned {result.deleted} superseded run(s); "
        f"kept {result.kept} latest-per-cell row(s)."
    )
    return 0


def format_analysis_run(run: AnalysisRun) -> str:
    """Per-ticker tally of a run, naming every ticker that failed.

    Printing 29 indicators for each of 506 codes is 15,000 numbers nobody reads.
    What the summary must still do is name a failure: a skip is a company with
    nothing mirrored, an error is ours.
    """
    counts: dict[AnalysisStatus, int] = {}
    for outcome in run.outcomes:
        counts[outcome.status] = counts.get(outcome.status, 0) + 1
    tally = ", ".join(f"{s.value}={n}" for s, n in sorted(counts.items()))

    lines: list[str] = ["", "=== Analysis run ==="]
    for outcome in run.outcomes:
        if outcome.status is AnalysisStatus.ERROR:
            lines.append(f"  !! {outcome.ticker:<8} {outcome.detail}")
    skipped = [o.ticker for o in run.outcomes if o.status is AnalysisStatus.SKIPPED]
    if skipped:
        lines.append(f"  skipped (nothing mirrored): {', '.join(sorted(skipped))}")
    views = sum(len(o.analyses) for o in run.outcomes)
    lines.append(
        f"--- {len(run.outcomes)} ticker(s), {views} view(s) stored | {tally or 'none'}"
    )
    return "\n".join(lines)


def format_batch_log(passes: list[YearPass]) -> str:
    """Per-year tally, with a line for every call that actually failed.

    A whole-exchange run makes ~3,300 calls a year, so the per-call log stops
    being a log and becomes a wall. What a summary must never do is hide a
    failure behind a number: a skip is counted (a company that did not file that
    year is the normal case), an error or an abort is named.
    """
    lines: list[str] = ["", "=== Collection log ==="]
    for pass_ in passes:
        counts: dict[OutcomeStatus, int] = {}
        for outcome in pass_.outcomes:
            counts[outcome.status] = counts.get(outcome.status, 0) + 1
        tally = ", ".join(f"{s.value}={n}" for s, n in sorted(counts.items()))
        resumed = (
            f", {pass_.already_mirrored} already mirrored"
            if pass_.already_mirrored
            else ""
        )
        lines.append(
            f"  {pass_.year}  {pass_.companies:>4} collected{resumed} "
            f"| {tally or 'nothing collected'}"
        )
        for outcome in pass_.outcomes:
            if outcome.status in _FAILED_STATUSES:
                lines.append(
                    f"    !! {outcome.ticker:<8} {outcome.module:<14} "
                    f"{outcome.status.value:<8} {outcome.detail}"
                )
    calls = sum(len(p.outcomes) for p in passes)
    failed = sum(1 for p in passes for o in p.outcomes if o.status in _FAILED_STATUSES)
    lines.append(f"--- {len(passes)} year(s), {calls} calls, {failed} failed")
    return "\n".join(lines)


def format_taxonomy_drift(drift: TaxonomyDrift, *, companies: int) -> str:
    """What moved in B3's classification since the snapshot was written.

    A changed sector leads, because it is the only one of the three that
    silently restates history: every stored analysis carries the classification
    it was computed under, so a company moving sector makes the persisted rows
    disagree with the snapshot until they are recomputed.
    """
    lines: list[str] = ["", "=== smaug taxonomy — B3 classification drift ==="]
    for ticker, before, after in drift.changed:
        lines.append(f"  ~~ {ticker:<8} {before.setor} -> {after.setor}")
        lines.append(f"     {before.subsetor} / {before.segmento}")
        lines.append(f"     {after.subsetor} / {after.segmento}")
    if drift.gained:
        lines.append(
            f"  ++ newly classified ({len(drift.gained)}): "
            f"{', '.join(drift.gained[:15])}"
            f"{' …' if len(drift.gained) > 15 else ''}"
        )
    if drift.lost:
        lines.append(
            f"  -- no longer classified ({len(drift.lost)}): "
            f"{', '.join(drift.lost[:15])}"
            f"{' …' if len(drift.lost) > 15 else ''}"
        )
    lines.append(
        f"--- {companies} companies asked | {drift.unchanged} unchanged, "
        f"{len(drift.gained)} gained, {len(drift.lost)} lost, "
        f"{len(drift.changed)} changed"
    )
    lines.append(
        f"    {drift.from_sheet} ticker(s) from B3's spreadsheet, "
        f"{drift.from_detail} from the per-company fallback (renamed since our "
        "FCA archive)"
    )
    if drift.unclassified:
        lines.append(
            f"    {len(drift.unclassified)} company(ies) B3 does not classify "
            "(judicial recovery, liquidation) — they keep the CVM fallback"
        )
    if drift.unknown_labels:
        lines.append("    !! labels no correction covers — verify before trusting:")
        for label in drift.unknown_labels:
            lines.append(f"       {label!r}")
    elif not drift.moved:
        lines.append("    the snapshot matches what B3 publishes today.")
    return "\n".join(lines)


def format_relink(report: RelinkReport) -> str:
    """Render what the relink stamped, and what it could not name."""
    lines: list[str] = ["", "=== smaug relink — mirror keyed on the registrant ==="]
    for ticker, count in sorted(report.linked.items()):
        lines.append(f"  {ticker:<8} {count:>6} document(s)")
    if not report.linked:
        lines.append("  (every CVM document already names its registrant)")
    lines.append(
        f"--- {report.documents} document(s) linked "
        f"across {len(report.linked)} ticker(s)"
    )
    if report.unresolved:
        who = ", ".join(report.unresolved)
        lines.append(f"    !! no registrant resolves: {who} — left on the ticker key")
    return "\n".join(lines)


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
            f" ({a.price_basis}, adjusted {_num(a.price_adjusted)})"
            if a.price_basis is not None
            else ""
        )
        lines.append(
            f"\n{a.ticker} [{a.classification.setor}] {a.view} "
            f"— ref {a.reference_date} — price {_num(a.price)}{basis}"
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


def format_doctor_summary(report: DoctorReport) -> str:
    """The coverage report as totals — the M0 gate at exchange scale.

    Same numbers as the per-cell listing, minus the cells. The one thing it does
    not compress is an **unclassified** null: a value we do not have and cannot
    explain is the only finding here that asks for work, so every ticker holding
    one is named. A named null is a fact about the world already, and a count of
    it is enough.
    """
    named: dict[NullReason, int] = {}
    values = unclassified = cells = exercises = 0
    analyzed = 0
    unnamed_tickers: set[str] = set()
    empty: list[str] = []

    for ticker_cov in report.tickers:
        if not ticker_cov.exercises:
            empty.append(ticker_cov.ticker)
            continue
        analyzed += 1
        for exercise in ticker_cov.exercises:
            exercises += 1
            for cell in exercise.indicators:
                cells += 1
                if cell.has_value:
                    values += 1
                elif cell.reason is not None:
                    named[cell.reason] = named.get(cell.reason, 0) + 1
                else:
                    unclassified += 1
                    unnamed_tickers.add(ticker_cov.ticker)

    share = (100 * values / cells) if cells else 0.0
    lines: list[str] = [
        "",
        "=== smaug doctor — persisted analysis coverage ===",
        f"  {analyzed} of {len(report.tickers)} ticker(s) analyzed, "
        f"{exercises} exercises, {cells} cells",
        f"  value={values} ({share:.1f}%) named={sum(named.values())} "
        f"unclassified={unclassified}",
    ]
    for reason, count in sorted(named.items(), key=lambda kv: -kv[1]):
        lines.append(f"    {reason.value:<26} {count:>7}")
    if empty:
        shown = ", ".join(sorted(empty)[:12])
        more = f" (+{len(empty) - 12} more)" if len(empty) > 12 else ""
        lines.append(f"  no persisted analysis: {shown}{more}")
    if unclassified:
        who = ", ".join(sorted(unnamed_tickers))
        lines.append(f"    !! {unclassified} unclassified nulls across: {who}")
    else:
        lines.append("    every null carries a named cause.")
    return "\n".join(lines)


def format_drift_summary(report: DriftReport) -> str:
    """Drift rolled up per account rather than per ticker.

    One company's account drifting is a fact about that filer. The same account
    drifting across two hundred of them is one bug in our mapping, and the
    per-ticker listing — 630 lines over the exchange — is the shape that hides
    exactly that. So the roll-up counts tickers per account, ordered by the side
    that is missing: ``newer`` first, because a needle that has stopped working
    on what we ingest today outranks one that never reached the old chart.
    """
    per_account: dict[str, dict[str, int]] = {}
    for ticker_drift in report.tickers:
        for drift in ticker_drift.accounts:
            sides = per_account.setdefault(
                drift.account, {"newer": 0, "older": 0, "mixed": 0}
            )
            sides[drift.missing_side] += 1

    lines: list[str] = ["", "=== smaug doctor — chart-of-accounts drift ==="]
    if not per_account:
        lines.append("  (no account changed status across any ticker's closed years)")
        return "\n".join(lines)

    order = {"newer": 0, "mixed": 1, "older": 2}

    def rank(item: tuple[str, dict[str, int]]) -> tuple[int, int, str]:
        account, sides = item
        worst = min(order[s] for s, n in sides.items() if n)
        return worst, -sum(sides.values()), account

    lines.append(f"  {'account':<24} {'tickers':>7}  newer  mixed  older")
    for account, sides in sorted(per_account.items(), key=rank):
        total = sum(sides.values())
        lines.append(
            f"  {account:<24} {total:>7}  {sides['newer']:>5}  "
            f"{sides['mixed']:>5}  {sides['older']:>5}"
        )
    lines.append(
        f"--- {report.drifting} account/ticker pairs changed status across "
        f"{len(per_account)} account(s). An account missing from every year is "
        "not drift and is not listed."
    )
    return "\n".join(lines)


def _years(years: tuple[int, ...]) -> str:
    """Compress a year list into ranges: ``2015-2019, 2021``."""
    if not years:
        return "—"
    runs: list[list[int]] = [[years[0]]]
    for year in years[1:]:
        if year == runs[-1][-1] + 1:
            runs[-1].append(year)
        else:
            runs.append([year])
    return ", ".join(
        str(run[0]) if len(run) == 1 else f"{run[0]}-{run[-1]}" for run in runs
    )


def format_drift(report: DriftReport) -> str:
    """Render the chart-of-accounts drift report (#156).

    Ordered by ``missing_side``: an account absent from the *newer* filings comes
    first, because a needle that has stopped working on what we ingest today is
    more urgent than one that never reached the old chart.
    """
    lines: list[str] = ["", "=== smaug doctor — chart-of-accounts drift ==="]
    order = {"newer": 0, "mixed": 1, "older": 2}
    for ticker_drift in report.tickers:
        if not ticker_drift.accounts:
            continue
        lines.append(f"\n{ticker_drift.ticker} [{_years(ticker_drift.years)}]")
        for drift in sorted(
            ticker_drift.accounts, key=lambda d: (order[d.missing_side], d.account)
        ):
            lines.append(
                f"    {drift.account:<22} missing {_years(drift.missing):<16} "
                f"read {_years(drift.read):<16} "
                f"({drift.missing_side}, {drift.boundaries} "
                f"{'boundary' if drift.boundaries == 1 else 'boundaries'})"
            )
    if not report.drifting:
        lines.append("\n  (no account changed status across any ticker's closed years)")
    else:
        lines.append(
            f"\n--- {report.drifting} accounts changed status. An account missing "
            "from every year is not drift and is not listed."
        )
    return "\n".join(lines)


def main() -> None:
    app()


if __name__ == "__main__":
    main()
