"""CLI entrypoints — the composition root for Phase 1.

Wires config -> Mongo -> source readers -> repository -> use cases, and renders
results to stdout. No business logic lives here: the commands only assemble
dependencies and call the use cases (plan §3.1 / ``src/smaug/AGENTS.md``).
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import Awaitable, Callable, Coroutine, Mapping, Sequence
from dataclasses import dataclass, replace
from datetime import date
from decimal import Decimal
from time import perf_counter
from typing import Any, cast

import httpx
import typer

from smaug.analysis.application.analyze import (
    AnalysisOutcome as AnalysisOutcome,
)
from smaug.analysis.application.analyze import (
    AnalysisRun as AnalysisRun,
)
from smaug.analysis.application.analyze import (
    AnalysisStatus as AnalysisStatus,
)
from smaug.analysis.application.analyze import (
    AnalyzePortfolioUseCase,
)
from smaug.analysis.application.doctor import (
    DebtCoverageSummary,
    DoctorReport,
    DoctorUseCase,
    ExerciseCoverage,
    TickerCoverage,
)
from smaug.analysis.application.drift import AccountDriftUseCase, DriftReport
from smaug.analysis.domain.entities import TickerAnalysis
from smaug.analysis.domain.financials import IssuerIdentity
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
from smaug.ingestion.application.failures import IngestionFailureService
from smaug.ingestion.application.ingest import (
    FailureContext,
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
from smaug.ingestion.application.runs import IngestionRunService
from smaug.ingestion.application.validation import (
    IngestionValidationService,
    RunValidationReporter,
)
from smaug.ingestion.domain.failures import FailureOccurrence
from smaug.ingestion.domain.ports import B3TapeObservation
from smaug.ingestion.domain.repositories import RawIngestionRepository
from smaug.ingestion.domain.runs import (
    IngestionRun,
    IngestionRunMetrics,
    IngestionRunParameters,
    IngestionRunStatus,
    ParserIdentity,
    TickerScope,
)
from smaug.ingestion.domain.validation import (
    BatchValidationReporter,
    IngestionValidationReport,
)
from smaug.ingestion.infrastructure.b3_capital_events import (
    CAPITAL_EVENT_B3_MODULE,
    B3CapitalEventSource,
)
from smaug.ingestion.infrastructure.b3_cash_dividends import (
    CASH_DIVIDEND_B3_MODULE,
    B3CashDividendSource,
)
from smaug.ingestion.infrastructure.b3_listed_company import (
    B3ListedCompany,
    B3ListedCompanyResolver,
)
from smaug.ingestion.infrastructure.b3_reused_roots import (
    REUSED_ROOT_TICKERS,
    B3ReusedRootRecovery,
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
from smaug.ingestion.infrastructure.repositories import (
    BeanieIngestionFailureRepository,
    BeanieIngestionRunRepository,
    BeanieIngestionValidationRepository,
    BeanieRawIngestionRepository,
)
from smaug.ingestion.infrastructure.routed_source import RoutedDataSource
from smaug.portfolio.application.refresh_taxonomy import (
    RefreshTaxonomyUseCase,
    TaxonomyDrift,
)
from smaug.portfolio.domain.company import (
    CompanyIdentity,
    UnitResolver,
    fundamental_exclusion,
    is_unit,
    per_share_components,
)
from smaug.portfolio.domain.fca_placeholders import (
    FcaPlaceholderFinding,
    FcaPlaceholderReport,
)
from smaug.portfolio.domain.provenance import FCA_SOURCE, FcaSnapshotProvenance
from smaug.portfolio.domain.sectors import Sector, sector_from_cvm
from smaug.portfolio.domain.securities import (
    RegistrantNamesResolver,
    SiblingCodesResolver,
)
from smaug.portfolio.domain.share_classes import (
    EconomicRightsStatus,
    PerShareClass,
    ShareClass,
    ShareClassMapping,
    ShareClassMappingStatus,
    TickerCodeEvidence,
    UnitComponent,
    mapping_for_share_class,
)
from smaug.portfolio.domain.taxonomy import (
    TAXONOMY_SNAPSHOT,
    Classification,
    classify,
)
from smaug.portfolio.domain.universe import ListedCompany
from smaug.portfolio.infrastructure.b3_taxonomy import B3TaxonomySource
from smaug.portfolio.infrastructure.cvm_registry import (
    CVM_FCA_BASE_URL,
    CvmCompanyRegistry,
)
from smaug.portfolio.infrastructure.cvm_securities import CvmSecurityHistory
from smaug.portfolio.infrastructure.fca_placeholders import (
    FcaPlaceholderRecovery,
    OfficialRegistrant,
    OfficialSecurityCode,
    QuoteSeries,
)
from smaug.shared.artifacts import SourceArtifact, SourceArtifactStore
from smaug.shared.build import application_commit
from smaug.shared.config import Settings, get_settings
from smaug.shared.db import init_database
from smaug.shared.errors import (
    IneligibleInstrumentError,
    UnknownTickerError,
)
from smaug.shared.events import EventBus
from smaug.shared.local_artifacts import LocalSourceArtifactStore
from smaug.shared.logging import get_logger
from smaug.shared.sql_db import create_engine, create_session_factory

app = typer.Typer(help="smaug — CVM/B3 ingestion and indicator analysis.")
logger = get_logger("smaug.cli")

_FAILED_STATUSES = frozenset(
    {OutcomeStatus.ERROR, OutcomeStatus.QUARANTINED, OutcomeStatus.ABORTED}
)
_DEFAULT_ARCHIVE_CONCURRENCY = 8


def _default_fca_provenance(settings: Settings) -> FcaSnapshotProvenance:
    """Describe the configured FCA snapshot before its archive is acquired."""
    return FcaSnapshotProvenance(
        year=settings.cvm_fca_year,
        source=FCA_SOURCE,
        source_url=(f"{CVM_FCA_BASE_URL}/fca_cia_aberta_{settings.cvm_fca_year}.zip"),
    )


def _remember_fca_provenance(
    collected: list[FcaSnapshotProvenance],
    provenance: FcaSnapshotProvenance,
) -> None:
    """Keep one deterministic provenance record for the selected FCA archive."""
    if provenance not in collected:
        collected.append(provenance)


@dataclass(frozen=True)
class YearPass:
    """What one archive year of a collection run produced."""

    year: int
    outcomes: list[FetchOutcome]
    companies: int
    already_mirrored: int


def _guarded[T](coro: Coroutine[Any, Any, T]) -> T:
    """Run a use-case coroutine, turning an invalid ticker into a clean exit.

    Keeps a raw registry-resolution failure off the terminal — the CLI reports a
    typo (or a not-yet-listed ticker) as one line, like the ingestion side maps
    a source's HTTP errors to typed ones.
    """
    try:
        return asyncio.run(coro)
    except (UnknownTickerError, IneligibleInstrumentError) as exc:
        typer.echo(f"error: {exc}", err=True)
        raise typer.Exit(code=2) from exc


def _resolve_scope(
    ticker: list[str] | None, all_listed: bool
) -> tuple[tuple[str, ...], bool]:
    """Resolve CLI scope, using the complete universe when no filter is given."""
    if all_listed and ticker:
        raise typer.BadParameter("--all and --ticker are mutually exclusive")
    tickers = tuple(ticker) if ticker else ()
    return tickers, all_listed or not tickers


async def _registry_identities(
    settings: Settings,
    http: httpx.AsyncClient,
    tickers: tuple[str, ...],
    artifact_store: SourceArtifactStore | None = None,
    fca_provenance: list[FcaSnapshotProvenance] | None = None,
    placeholder_reports: list[FcaPlaceholderReport] | None = None,
    fca_year: int | None = None,
) -> dict[str, CompanyIdentity]:
    """Resolve every requested ticker via the CVM FCA registry (#212).

    The registry reads the independently configured current FCA snapshot;
    ``settings.cvm_year`` remains reserved for the accounting archive selected
    by the caller.

    No hand-picked shortcut: every ticker, including the nine that used to skip
    this call, resolves through a live FCA download/parse. A ticker that
    resolves nowhere is a user error — a typo, or a company CVM does not list.
    A known security outside current fundamental analysis is rejected separately,
    naming its FCA type or trading end date (ADR 0053).
    """
    registry = CvmCompanyRegistry(
        http,
        year=fca_year or settings.cvm_fca_year,
        cache_dir=settings.cvm_cache_dir,
        artifact_store=artifact_store,
    )
    _configure_placeholder_recovery(registry, settings, http)
    identities = await registry.resolve_all(tickers)
    await _remember_placeholder_report(registry, placeholder_reports)
    if fca_provenance is not None:
        _remember_fca_provenance(fca_provenance, await registry.provenance())
    for ticker in tickers:
        identity = identities.get(ticker)
        if identity is None:
            raise UnknownTickerError(ticker)
        exclusion = fundamental_exclusion(identity)
        if exclusion is not None:
            raise IneligibleInstrumentError(ticker, exclusion)
    return identities


def _sector_resolver(
    identities: dict[str, CompanyIdentity],
) -> Callable[[str], Sector]:
    """A ``Sector`` for any requested ticker: the CVM activity label folded to
    the enum (``sector_from_cvm``), unconditionally (#212)."""

    def resolve(ticker: str) -> Sector:
        identity = identities.get(ticker)
        if identity is None:
            raise UnknownTickerError(ticker)
        return sector_from_cvm(identity.cvm_sector)

    return resolve


def _registrant_resolver(
    identities: dict[str, CompanyIdentity],
) -> Callable[[str], str | None]:
    """The registrant whose filings a ticker reads (``CD_CVM``, ADR 0030),
    resolved from the registry unconditionally (#212)."""

    def resolve(ticker: str) -> str | None:
        identity = identities.get(ticker)
        return identity.cd_cvm if identity is not None else None

    return resolve


def _issuer_resolver(
    identities: dict[str, CompanyIdentity],
) -> Callable[[str], IssuerIdentity | None]:
    """Resolve the complete issuer identity used by persisted debt evidence."""

    def resolve(ticker: str) -> IssuerIdentity | None:
        identity = identities.get(ticker)
        if identity is None:
            return None
        return IssuerIdentity(
            cd_cvm=identity.cd_cvm,
            cnpj=identity.cnpj,
            issuer_name=identity.denom,
        )

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
    """When a ticker was listed, from the FCA's ``Data_Inicio_Listagem`` (#153,
    #212) — the same registry every other resolver here reads."""

    def resolve(ticker: str) -> date | None:
        identity = identities.get(ticker)
        return identity.listed_since if identity is not None else None

    return resolve


def _classes_resolver(
    identities: dict[str, CompanyIdentity],
) -> Callable[[str], tuple[ShareClass, ...]]:
    """The listed ON/PN classes for the cap, from the FCA (ADR 0014, #212)."""

    def resolve(ticker: str) -> tuple[ShareClass, ...]:
        identity = identities.get(ticker)
        return identity.share_classes if identity is not None else ()

    return resolve


def _class_mappings_resolver(
    identities: dict[str, CompanyIdentity],
    historical_codes: Callable[[str], tuple[TickerCodeEvidence, ...]],
) -> Callable[[str], tuple[ShareClassMapping, ...]]:
    """Combine current FCA class identity with every historical FCA code."""

    def resolve(ticker: str) -> tuple[ShareClassMapping, ...]:
        identity = identities.get(ticker)
        if identity is None:
            return ()
        mappings = identity.share_class_mappings
        if not mappings:
            mappings = tuple(
                mapping_for_share_class(identity.cnpj, share_class)
                for share_class in identity.share_classes
            )
        enriched: list[ShareClassMapping] = []
        for mapping in mappings:
            if mapping.symbol is None:
                historical: dict[str, TickerCodeEvidence] = {}
                for code in mapping.code_evidence:
                    for code_evidence in historical_codes(code.symbol):
                        historical[code_evidence.symbol] = code_evidence
                enriched.append(
                    replace(
                        mapping,
                        code_evidence=(
                            tuple(historical.values()) or mapping.code_evidence
                        ),
                    )
                )
                continue
            historical_evidence = historical_codes(mapping.symbol)
            enriched.append(
                replace(
                    mapping,
                    code_evidence=historical_evidence or mapping.code_evidence,
                )
            )
        return tuple(enriched)

    return resolve


def _unit_composition_resolver(
    identities: dict[str, CompanyIdentity],
) -> Callable[[str], int | None]:
    """Underlying shares one unit bundles, from the FCA's parsed ratio (#212).

    Generalizes the old hand-picked ``UNIT_COMPOSITION`` (SAPR11/TAEE11 only) to
    any unit ticker the FCA lists — Klabin's KLBN11 included.
    """

    def resolve(ticker: str) -> int | None:
        identity = identities.get(ticker)
        return identity.shares_per_unit if identity is not None else None

    return resolve


def _per_share_resolver(
    identities: dict[str, CompanyIdentity],
) -> Callable[[str], tuple[UnitComponent, ...]]:
    """The CPC 41 class or FCA unit bundle represented by each ticker."""

    def resolve(ticker: str) -> tuple[UnitComponent, ...]:
        identity = identities.get(ticker)
        return per_share_components(identity) if identity is not None else ()

    return resolve


def _per_share_classes_resolver(
    identities: dict[str, CompanyIdentity],
) -> Callable[[str], tuple[PerShareClass, ...]]:
    """All listed economic classes whose CPC 41 leaves must reconcile."""

    def resolve(ticker: str) -> tuple[PerShareClass, ...]:
        identity = identities.get(ticker)
        return (
            tuple(share_class.per_share_class for share_class in identity.share_classes)
            if identity is not None
            else ()
        )

    return resolve


def _per_share_rights_reason_resolver(
    identities: dict[str, CompanyIdentity],
) -> Callable[[str], NullReason]:
    """Keep unresolved FCA identity distinct from absent CPC41 rights."""

    def resolve(ticker: str) -> NullReason:
        identity = identities.get(ticker)
        if identity is not None and any(
            mapping.status is ShareClassMappingStatus.UNRESOLVED
            for mapping in identity.share_class_mappings
        ):
            return NullReason.UNRESOLVED_SHARE_CLASS
        if identity is not None and any(
            mapping.economic_rights is EconomicRightsStatus.UNRESOLVED
            for mapping in identity.share_class_mappings
        ):
            return NullReason.MISSING_ECONOMIC_RIGHTS
        return NullReason.MISSING_ECONOMIC_RIGHTS

    return resolve


def _unit_resolver(identities: dict[str, CompanyIdentity]) -> UnitResolver:
    """Whether a ticker's FCA-resolved security type is a unit (ADR 0053)."""

    def resolve(ticker: str) -> bool:
        identity = identities.get(ticker)
        return identity is not None and is_unit(identity)

    return resolve


async def _cvm_key_maps(
    settings: Settings,
    http: httpx.AsyncClient,
    tickers: tuple[str, ...],
    artifact_store: SourceArtifactStore,
    fca_provenance: list[FcaSnapshotProvenance] | None = None,
    fca_year: int | None = None,
) -> tuple[dict[str, str], dict[str, str]]:
    """The ticker -> CD_CVM and ticker -> CNPJ maps the CVM sources need.

    Registry-resolved for every ticker, unconditionally (#212).
    """
    identities = await _registry_identities(
        settings,
        http,
        tickers,
        artifact_store,
        fca_provenance=fca_provenance,
        fca_year=fca_year,
    )
    code = {t: i.cd_cvm for t, i in identities.items()}
    cnpj = {t: i.cnpj for t, i in identities.items()}
    return code, cnpj


@app.command()
def ingest(
    ticker: list[str] | None = typer.Option(
        None,
        "--ticker",
        "-t",
        help="Ticker to collect (repeatable). Default: every listed company.",
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
        help="Re-collect a mirrored company; identical filings are recorded unchanged.",
    ),
    verbose: bool = typer.Option(
        False, "--verbose", "-v", help="Log every call instead of a per-year summary."
    ),
    concurrency: int = typer.Option(
        _DEFAULT_ARCHIVE_CONCURRENCY,
        "--concurrency",
        min=1,
        help="Maximum concurrent CVM archive workers; live B3 modules remain serial.",
    ),
) -> None:
    """Collect the configured modules for the active source and store the mirror.

    Two scopes: an explicit ``--ticker`` list, or every company the CVM registry
    lists (the default, also available as ``--all``). A run over 368 companies
    and eleven years is one command, because each year's archive is read once and
    served to every company in it (``--from-year``/``--to-year``).
    """
    years = _years_to_sweep(year, from_year, to_year)
    tickers, whole_exchange = _resolve_scope(ticker, all_listed)
    ticker_scope = TickerScope.ALL if whole_exchange else TickerScope.EXPLICIT
    try:
        # _guarded turns an unknown ticker into a clean exit, like analyze (#13).
        exit_code = _guarded(
            _run_ingest(
                tickers,
                document=document,
                years=years,
                whole_exchange=whole_exchange,
                force=force,
                verbose=verbose,
                concurrency=concurrency,
                ticker_scope=ticker_scope,
            )
        )
    except NotImplementedError as exc:
        typer.echo(f"error: {exc}", err=True)
        raise typer.Exit(code=2) from exc
    raise typer.Exit(code=exit_code)


@app.command("b3-reused-root-backfill")
def b3_reused_root_backfill(
    ticker: list[str] | None = typer.Option(
        None,
        "--ticker",
        "-t",
        help="Affected security to repair (repeatable; default: all three).",
    ),
    fca_year: int | None = typer.Option(
        None,
        "--fca-year",
        help="Historical FCA snapshot year (default: configured accounting year).",
    ),
    verbose: bool = typer.Option(
        False, "--verbose", "-v", help="Log every backfill call."
    ),
    concurrency: int = typer.Option(
        _DEFAULT_ARCHIVE_CONCURRENCY,
        "--concurrency",
        min=1,
        help="Maximum worker count; B3 calls remain serial.",
    ),
) -> None:
    """Backfill only the three B3 roots proven to have changed registrants."""
    selected = tuple(
        dict.fromkeys(
            code.strip().upper()
            for code in (ticker or tuple(sorted(REUSED_ROOT_TICKERS)))
        )
    )
    unsupported = tuple(code for code in selected if code not in REUSED_ROOT_TICKERS)
    if unsupported:
        raise typer.BadParameter(
            "--ticker is limited to JBSS3, PETZ3 and MOAR3: " + ", ".join(unsupported)
        )
    settings = get_settings()
    exit_code = _guarded(
        _run_ingest(
            selected,
            document=settings.cvm_document,
            years=(None,),
            force=True,
            verbose=verbose,
            concurrency=concurrency,
            ticker_scope=TickerScope.EXPLICIT,
            modules=(CAPITAL_EVENT_B3_MODULE, CASH_DIVIDEND_B3_MODULE),
            identity_year=fca_year or settings.cvm_year,
            reused_root_recovery=True,
        )
    )
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


@app.command("ingestion-resume")
def ingestion_resume(
    run_id: str = typer.Option(
        ..., "--run-id", help="Run whose failed calls to retry."
    ),
    retry_permanent: bool = typer.Option(
        False,
        "--retry-permanent",
        help="Explicitly retry recorded permanent absences.",
    ),
) -> None:
    """Retry eligible failed calls from one previous ingestion run."""
    try:
        exit_code = _guarded(_run_ingestion_resume(run_id, retry_permanent))
    except (LookupError, ValueError) as exc:
        typer.echo(f"error: {exc}", err=True)
        raise typer.Exit(code=1) from exc
    raise typer.Exit(code=exit_code)


async def _run_ingestion_resume(run_id: str, retry_permanent: bool) -> int:
    """Select one run's safe retries and compose a new, narrower ingestion run."""
    settings = get_settings()
    client = await init_database(settings)
    try:
        run_service = IngestionRunService(BeanieIngestionRunRepository())
        parent = await run_service.get(run_id)
        if parent is None:
            raise LookupError(f"ingestion run not found: {run_id}")
        failure_service = IngestionFailureService(BeanieIngestionFailureRepository())
        failures = await failure_service.eligible_for_run(
            run_id,
            current_parsers=_parser_by_module(parent.parameters.modules),
            current_sources=_source_by_module(parent.parameters.modules),
            retry_permanent=retry_permanent,
        )
    finally:
        await client.close()

    if not failures:
        typer.echo("No eligible failed calls for this run.")
        return 0

    mutable_plan: dict[int, dict[str, list[str]]] = {}
    retry_failure_ids: dict[tuple[str, str, int], str] = {}
    for failure in failures:
        planned_modules = mutable_plan.setdefault(failure.year, {}).setdefault(
            failure.ticker, []
        )
        planned_modules.append(failure.module)
        retry_failure_ids[(failure.ticker, failure.module, failure.year)] = (
            failure.failure_id
        )
    call_plan = {
        year: {ticker: tuple(modules) for ticker, modules in plan.items()}
        for year, plan in mutable_plan.items()
    }
    tickers = tuple(dict.fromkeys(failure.ticker for failure in failures))
    resumed_modules = tuple(dict.fromkeys(failure.module for failure in failures))
    typer.echo(f"Resuming {len(failures)} failed call(s) from run {run_id}.")
    return await _run_ingest(
        tickers,
        document=parent.parameters.document,
        years=tuple(call_plan),
        force=True,
        verbose=True,
        concurrency=parent.parameters.concurrency,
        ticker_scope=TickerScope.EXPLICIT,
        modules=resumed_modules,
        call_plan=call_plan,
        retry_failure_ids=retry_failure_ids,
    )


@app.command()
def report(
    ticker: list[str] | None = typer.Option(
        None,
        "--ticker",
        "-t",
        help="Ticker to report (repeatable). Required unless --all is used.",
    ),
    all_listed: bool = typer.Option(
        False, "--all", "-a", help="Every traded code the CVM registry lists."
    ),
) -> None:
    """Print completeness for an explicit ticker subset or the whole universe."""
    if not ticker and not all_listed:
        raise typer.BadParameter("provide --ticker or --all")
    tickers, whole_exchange = _resolve_scope(ticker, all_listed)
    _guarded(_run_report(tickers, whole_exchange=whole_exchange))


def _build_data_source(
    settings: Settings,
    http: httpx.AsyncClient,
    ticker_to_code: dict[str, str],
    ticker_to_cnpj: dict[str, str],
    *,
    document: str | None = None,
    year: int | None = None,
    artifact_store: SourceArtifactStore | None = None,
    validation_reporter: BatchValidationReporter | None = None,
    reused_root_recovery: B3ReusedRootRecovery | None = None,
) -> RoutedDataSource:
    """Build the raw source: CVM's archives, with B3's endpoints routed per module.

    ``document``/``year`` override the config for one run (e.g. to pull several
    CVM files). The CVM key maps are resolved upstream, via the FCA registry.
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
        artifact_store=artifact_store,
        validation_reporter=validation_reporter,
    )
    # The share counts live in a different CVM archive (FRE), keyed by CNPJ.
    capital = CvmCapitalSource(
        http,
        ticker_to_cnpj,
        year=cvm_year,
        cache_dir=settings.cvm_cache_dir,
        ticker_to_code=ticker_to_code,
        artifact_store=artifact_store,
        validation_reporter=validation_reporter,
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
        artifact_store=artifact_store,
        validation_reporter=validation_reporter,
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
        artifact_store=artifact_store,
        validation_reporter=validation_reporter,
    )
    # ...and B3 declares the same events without the counts, but *with* the last
    # session quoted on the old base — the two are complementary, and neither
    # covers the other's years (ADR 0034).
    exchange_events = B3CapitalEventSource(
        http,
        ticker_to_code=ticker_to_code,
        base_url=settings.b3_listed_base_url,
        validation_reporter=validation_reporter,
        reused_root_recovery=reused_root_recovery,
    )
    # ...and the cash it paid out, which no price file carries and which the
    # third price basis is rebuilt from (ADR 0039).
    cash_dividends = B3CashDividendSource(
        http,
        ticker_to_code=ticker_to_code,
        base_url=settings.b3_listed_base_url,
        validation_reporter=validation_reporter,
        reused_root_recovery=reused_root_recovery,
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
    concurrency: int = _DEFAULT_ARCHIVE_CONCURRENCY,
    ticker_scope: TickerScope = TickerScope.EXPLICIT,
    modules: tuple[str, ...] | None = None,
    call_plan: dict[int, dict[str, tuple[str, ...]]] | None = None,
    retry_failure_ids: dict[tuple[str, str, int], str] | None = None,
    identity_year: int | None = None,
    reused_root_recovery: bool = False,
) -> int:
    settings = get_settings()
    tickers = tuple(dict.fromkeys(tickers))
    active_modules = modules or tuple(settings.cvm_modules)
    retry_failure_ids = retry_failure_ids or {}
    client = await init_database(settings)
    repository = BeanieRawIngestionRepository()
    run_service = IngestionRunService(BeanieIngestionRunRepository())
    failure_service = IngestionFailureService(BeanieIngestionFailureRepository())
    validation_service = IngestionValidationService(
        BeanieIngestionValidationRepository()
    )
    passes: list[YearPass] = []
    outcomes: list[FetchOutcome] = []
    fca_provenance: list[FcaSnapshotProvenance] = []
    effective_years = tuple(year or settings.cvm_year for year in years)
    effective_document = (document or settings.cvm_document).upper()
    parameters = IngestionRunParameters(
        ticker_scope=ticker_scope,
        tickers=() if whole_exchange else tickers,
        years=effective_years,
        document=effective_document,
        modules=active_modules,
        force=force,
        verbose=verbose,
        concurrency=concurrency,
    )

    completed_run_id: str | None = None

    async def collect(
        run_id: str,
        lifecycle_sink: Callable[[FetchOutcome], Awaitable[None]],
    ) -> None:
        nonlocal completed_run_id
        completed_run_id = run_id

        async def outcome_sink(outcome: FetchOutcome) -> None:
            outcomes.append(outcome)
            await lifecycle_sink(outcome)

        selected_tickers = tickers

        async def exclusion_sink(count: int) -> None:
            await run_service.exclude_calls(run_id, count)

        async def metrics_sink(metrics: IngestionRunMetrics) -> None:
            await run_service.record_metrics(run_id, metrics)

        async with httpx.AsyncClient(timeout=30.0) as http:
            validation_reporter = RunValidationReporter(validation_service, run_id)

            async def artifact_observer(artifact: SourceArtifact) -> None:
                await run_service.record_artifact(run_id, artifact.artifact_id)

            artifact_store = LocalSourceArtifactStore(
                http,
                settings.source_artifact_dir,
                observer=artifact_observer,
            )
            companies = (
                await _universe(
                    settings,
                    http,
                    artifact_store,
                    fca_provenance=fca_provenance,
                )
                if whole_exchange
                else ()
            )
            if whole_exchange:
                selected_tickers = tuple(company.ticker for company in companies)
                await run_service.resolve_tickers(run_id, selected_tickers)
            planned = (
                sum(len(owed) for plan in call_plan.values() for owed in plan.values())
                if call_plan is not None
                else len(selected_tickers) * len(active_modules) * len(years)
            )
            await run_service.plan_calls(run_id, planned)
            for year in years:
                effective_year = year or settings.cvm_year
                passes.append(
                    await _ingest_one_year(
                        settings,
                        http,
                        repository,
                        selected_tickers,
                        companies,
                        document=document,
                        year=year,
                        whole_exchange=whole_exchange,
                        force=force,
                        run_id=run_id,
                        outcome_sink=outcome_sink,
                        exclusion_sink=exclusion_sink,
                        metrics_sink=metrics_sink,
                        artifact_store=artifact_store,
                        validation_reporter=validation_reporter,
                        fca_provenance=fca_provenance,
                        modules=active_modules,
                        identity_year=identity_year,
                        reused_root_recovery=reused_root_recovery,
                        call_plan=(
                            call_plan.get(effective_year)
                            if call_plan is not None
                            else None
                        ),
                        failure_service=failure_service,
                        retry_failure_ids=retry_failure_ids,
                        concurrency=concurrency,
                    )
                )

    run: IngestionRun | None = None
    try:
        await run_service.execute(
            parameters,
            application_commit=application_commit(),
            parsers=_parser_identities(active_modules),
            operation=collect,
        )
        if completed_run_id is None:
            raise AssertionError("ingestion run completed without a run id")
        run = await run_service.get(completed_run_id)
    finally:
        await client.close()

    if run is None:
        raise AssertionError("completed ingestion run is not persisted")
    logger.info(
        "%s",
        json.dumps(_metrics_log_event(run), sort_keys=True, separators=(",", ":")),
    )
    collection_log = (
        _format_collection_log(outcomes) if verbose else format_batch_log(passes)
    )
    snapshot = (
        fca_provenance[0] if fca_provenance else _default_fca_provenance(settings)
    )
    print(
        f"{format_fca_snapshot(snapshot)}\n"
        f"{collection_log}\n{format_ingestion_metrics(run)}"
    )
    return 1 if any(o.status in _FAILED_STATUSES for o in outcomes) else 0


async def _universe(
    settings: Settings,
    http: httpx.AsyncClient,
    artifact_store: SourceArtifactStore | None = None,
    fca_provenance: list[FcaSnapshotProvenance] | None = None,
) -> tuple[ListedCompany, ...]:
    """Every listed company from the current FCA snapshot (#109)."""
    registry = CvmCompanyRegistry(
        http,
        year=settings.cvm_fca_year,
        cache_dir=settings.cvm_cache_dir,
        artifact_store=artifact_store,
    )
    companies = await registry.companies()
    if fca_provenance is not None:
        _remember_fca_provenance(fca_provenance, await registry.provenance())
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
    run_id: str,
    outcome_sink: Callable[[FetchOutcome], Awaitable[None]],
    exclusion_sink: Callable[[int], Awaitable[None]],
    metrics_sink: Callable[[IngestionRunMetrics], Awaitable[None]],
    artifact_store: SourceArtifactStore,
    validation_reporter: BatchValidationReporter,
    fca_provenance: list[FcaSnapshotProvenance] | None,
    modules: tuple[str, ...],
    identity_year: int | None,
    reused_root_recovery: bool,
    call_plan: dict[str, tuple[str, ...]] | None,
    failure_service: IngestionFailureService,
    retry_failure_ids: dict[tuple[str, str, int], str],
    concurrency: int,
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
        code_map, cnpj_map = await _cvm_key_maps(
            settings,
            http,
            tickers,
            artifact_store,
            fca_provenance=fca_provenance,
            fca_year=identity_year,
        )
        wanted = tickers

    recovery = None
    if reused_root_recovery:
        recovery = B3ReusedRootRecovery(
            ticker_to_code=code_map,
            ticker_to_cnpj=cnpj_map,
            tape=_CotahistTapeAdapter(_build_archive(settings, http)),
        )
    source = _build_data_source(
        settings,
        http,
        code_map,
        cnpj_map,
        document=document,
        year=year,
        artifact_store=artifact_store,
        validation_reporter=validation_reporter,
        reused_root_recovery=recovery,
    )
    artifacts: dict[str, SourceArtifact | None] = {}
    if whole_exchange and not force:
        # The resume plan already needs every archive identity. Measuring that
        # acquisition here adds no source call and preserves the normal abort
        # semantics of portfolio/forced runs, which fetch inside the use case.
        artifacts = await _measure_archives(
            source,
            modules,
            run_id=run_id,
            year=year if year is not None else settings.cvm_year,
            metrics_sink=metrics_sink,
        )
    plan = dict.fromkeys(wanted, modules)
    if call_plan is not None:
        plan = call_plan
    elif whole_exchange and not force:
        plan = await _work_plan(
            repository, source, wanted, code_map, modules, artifacts=artifacts
        )
    scheduled = sum(len(owed) for owed in plan.values())
    if call_plan is None:
        await exclusion_sink(len(wanted) * len(modules) - scheduled)

    effective_year = year if year is not None else settings.cvm_year
    parsers = _parser_by_module(modules)
    sources = _source_by_module(modules)

    async def artifact_id_for(module: str) -> str | None:
        artifact = await source.artifact_for(module)
        return artifact.artifact_id if artifact is not None else None

    async def failure_sink(occurrence: FailureOccurrence) -> None:
        key = (occurrence.ticker, occurrence.module, occurrence.year)
        await failure_service.record(
            run_id,
            occurrence,
            retry_of=retry_failure_ids.get(key),
        )

    async def resolution_sink(ticker: str, module: str) -> None:
        failure_id = retry_failure_ids.get((ticker, module, effective_year))
        if failure_id is not None:
            await failure_service.resolve(failure_id, run_id=run_id)

    failure_context = FailureContext(
        year=effective_year,
        registrants=code_map,
        sources=sources,
        parsers=parsers,
        artifact_id_for=artifact_id_for,
    )

    outcomes: list[FetchOutcome] = []
    for owed, tickers in _by_owed_modules(plan):
        use_case = IngestPortfolioUseCase(
            client=source,
            repository=repository,
            event_bus=EventBus(),
            modules=owed,
            run_id=run_id,
            # Only these two hit a live, per-ticker B3 endpoint
            # (``GetListedSupplementCompany``, ADR 0034/ADR 0039); every other
            # module reads this year's already-downloaded CVM archive from
            # memory and owes the call no pause at all (#214).
            paced_modules=frozenset({CAPITAL_EVENT_B3_MODULE, CASH_DIVIDEND_B3_MODULE}),
            max_concurrency=concurrency,
            outcome_sink=outcome_sink,
            failure_sink=failure_sink,
            resolution_sink=resolution_sink,
            failure_context=failure_context,
        )
        outcomes.extend(await use_case.execute(tickers))
        if any(o.status is OutcomeStatus.ABORTED for o in outcomes):
            # The use case stops the run on a fatal error; the next group would
            # meet the same one (a dead ZIP is dead for every module).
            break
    return YearPass(
        year=effective_year,
        outcomes=outcomes,
        companies=len(plan),
        already_mirrored=len(wanted) - len(plan),
    )


async def _measure_archives(
    source: RoutedDataSource,
    modules: Sequence[str],
    *,
    run_id: str,
    year: int,
    metrics_sink: Callable[[IngestionRunMetrics], Awaitable[None]],
) -> dict[str, SourceArtifact | None]:
    """Acquire every archive once and record the reproducible source footprint."""
    artifacts: dict[str, SourceArtifact | None] = {}
    seen: set[str] = set()
    for module in dict.fromkeys(modules):
        started = perf_counter()
        artifact = await source.artifact_for(module)
        download_seconds = perf_counter() - started
        artifacts[module] = artifact
        if artifact is None:
            continue
        cached = artifact.artifact_id in seen
        seen.add(artifact.artifact_id)
        metrics = IngestionRunMetrics(
            download_seconds=download_seconds,
            archive_bytes=0 if cached else artifact.byte_size,
            cache_hits=int(cached),
            cache_misses=int(not cached),
        )
        await metrics_sink(metrics)
        logger.info(
            "%s",
            json.dumps(
                {
                    "event": "ingestion.archive",
                    "run_id": run_id,
                    "year": year,
                    "module": module,
                    "artifact_id": artifact.artifact_id,
                    "download_seconds": download_seconds,
                    "archive_bytes": 0 if cached else artifact.byte_size,
                    "cache_hit": cached,
                },
                sort_keys=True,
                separators=(",", ":"),
            ),
        )
    return artifacts


def _parser_identities(modules: Sequence[str]) -> tuple[ParserIdentity, ...]:
    """Stable parser catalog matching the composition root's module routes."""
    return tuple(dict.fromkeys(_parser_by_module(modules).values()))


_MODULE_ADAPTERS = {
    CAPITAL_MODULE: (CvmCapitalSource.parser_identity, CvmCapitalSource.source),
    TREASURY_MODULE: (CvmTreasurySource.parser_identity, CvmTreasurySource.source),
    CAPITAL_EVENT_MODULE: (
        CvmCapitalEventSource.parser_identity,
        CvmCapitalEventSource.source,
    ),
    CAPITAL_EVENT_B3_MODULE: (
        B3CapitalEventSource.parser_identity,
        B3CapitalEventSource.source,
    ),
    CASH_DIVIDEND_B3_MODULE: (
        B3CashDividendSource.parser_identity,
        B3CashDividendSource.source,
    ),
}
_DEFAULT_ADAPTER = (CvmDataSource.parser_identity, CvmDataSource.source)


def _parser_by_module(modules: Sequence[str]) -> dict[str, ParserIdentity]:
    """Current parser identity for every requested module."""
    return {
        module: _MODULE_ADAPTERS.get(module.upper(), _DEFAULT_ADAPTER)[0]
        for module in modules
    }


def _source_by_module(modules: Sequence[str]) -> dict[str, str]:
    """Name the public source endpoint backing each configured module."""
    return {
        module: _MODULE_ADAPTERS.get(module.upper(), _DEFAULT_ADAPTER)[1]
        for module in modules
    }


@app.command("ingestion-runs")
def ingestion_runs(
    run_id: str | None = typer.Option(None, "--run-id", help="Show one run by id."),
    limit: int = typer.Option(10, "--limit", min=1, help="Number of recent runs."),
) -> None:
    """Read durable ingestion-run summaries for local diagnosis."""
    runs = _guarded(_run_ingestion_runs(run_id, limit))
    if run_id is not None and not runs:
        typer.echo(f"error: ingestion run not found: {run_id}", err=True)
        raise typer.Exit(code=1)
    print(format_ingestion_runs(runs))


@app.command("ingestion-validations")
def ingestion_validations(
    run_id: str | None = typer.Option(None, "--run-id", help="Filter by run id."),
    limit: int = typer.Option(20, "--limit", min=1, help="Number of reports."),
    approve: str | None = typer.Option(
        None,
        "--approve",
        help="Approve one quarantine after review; it never releases raw data.",
    ),
    note: str = typer.Option("", "--note", help="Operator approval rationale."),
) -> None:
    """Inspect source-batch validation reports and record an operator review."""
    try:
        reports = _guarded(_run_ingestion_validations(run_id, limit, approve, note))
    except (LookupError, ValueError) as exc:
        typer.echo(f"error: {exc}", err=True)
        raise typer.Exit(code=1) from exc
    print(format_ingestion_validations(reports))


async def _run_ingestion_runs(
    run_id: str | None, limit: int
) -> tuple[IngestionRun, ...]:
    settings = get_settings()
    client = await init_database(settings)
    try:
        service = IngestionRunService(BeanieIngestionRunRepository())
        if run_id is not None:
            run = await service.get(run_id)
            return (run,) if run is not None else ()
        return await service.recent(limit)
    finally:
        await client.close()


async def _run_ingestion_validations(
    run_id: str | None,
    limit: int,
    approve: str | None,
    note: str,
) -> tuple[IngestionValidationReport, ...]:
    settings = get_settings()
    client = await init_database(settings)
    try:
        service = IngestionValidationService(BeanieIngestionValidationRepository())
        if approve is not None:
            await service.approve(approve, note)
        return await service.recent(limit, run_id=run_id)
    finally:
        await client.close()


async def _work_plan(
    repository: RawIngestionRepository,
    source: RoutedDataSource,
    wanted: tuple[str, ...],
    code_map: dict[str, str],
    modules: Sequence[str],
    *,
    artifacts: dict[str, SourceArtifact | None] | None = None,
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
    artifacts = artifacts or {
        module: await source.artifact_for(module) for module in modules
    }
    done: dict[str, set[str]] = {}
    sources = _source_by_module(modules)
    for module, artifact in artifacts.items():
        done[module] = await repository.mirrored_for(
            module,
            source=sources[module],
            artifact_id=artifact.artifact_id if artifact is not None else None,
        )
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


async def _run_report(
    tickers: tuple[str, ...], *, whole_exchange: bool = False
) -> None:
    settings = get_settings()
    client = await init_database(settings)
    try:
        async with httpx.AsyncClient(timeout=30.0) as http:
            if whole_exchange:
                tickers, identities = await _universe_tickers(settings, http)
            else:
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
        None,
        "--ticker",
        "-t",
        help="Ticker to analyze (repeatable). Default: every traded code.",
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
    With no flags, the command analyzes the complete traded-code universe, the
    same scope selected explicitly by ``--all``.
    """
    tickers, whole_exchange = _resolve_scope(ticker, all_listed)
    exit_code = _guarded(
        _run_analyze(tickers, whole_exchange=whole_exchange, verbose=verbose)
    )
    raise typer.Exit(code=exit_code)


async def _security_resolvers(
    settings: Settings,
    http: httpx.AsyncClient,
    *,
    artifact_store: SourceArtifactStore | None = None,
    snapshot: FcaSnapshotProvenance | None = None,
) -> tuple[
    SiblingCodesResolver,
    RegistrantNamesResolver,
    Callable[[str], tuple[TickerCodeEvidence, ...]],
]:
    """What the cadastre knows about a security's identity, in two answers.

    The codes each share class has been filed under (#193), read across every FCA
    year that carries the trading code — which is 2018 on — through the current
    FCA snapshot. And every name each registrant has filed, which is what
    confirms a code retired before that column existed, against the name B3
    printed beside it (#198).
    """
    history = CvmSecurityHistory(
        http,
        # Through today rather than the mirrored year: a code renamed this year
        # is named by no earlier archive, and the running year's file is skipped
        # if CVM has not published it yet.
        through=max(settings.cvm_fca_year, date.today().year),
        cache_dir=settings.cvm_cache_dir,
        artifact_store=artifact_store,
        snapshot_year=snapshot.year if snapshot is not None else None,
        snapshot_artifact_id=(snapshot.artifact_id if snapshot is not None else None),
    )
    return (
        await history.resolver(),
        await history.names(),
        await history.historical_codes(),
    )


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


def _configure_placeholder_recovery(
    registry: CvmCompanyRegistry,
    settings: Settings,
    http: httpx.AsyncClient,
) -> None:
    """Attach the real FCA placeholder producer at the composition root."""
    setter = getattr(registry, "set_placeholder_recoverer", None)
    if not callable(setter):
        return
    b3 = B3ListedCompanyResolver(http, base_url=settings.b3_listed_base_url)
    setter(
        FcaPlaceholderRecovery(
            _B3RegistrantAdapter(b3),
            _CotahistArchiveAdapter(_build_archive(settings, http)),
            snapshot_year=settings.cvm_fca_year,
        )
    )


@dataclass(frozen=True)
class _B3RegistrantAdapter:
    """Translate the existing B3 source into the portfolio boundary."""

    resolver: B3ListedCompanyResolver

    async def resolve_by_cvm(
        self, cvm_code: str, *, cnpj: str | None = None
    ) -> OfficialRegistrant:
        company = await self.resolver.resolve_by_cvm(cvm_code, cnpj=cnpj)
        return OfficialRegistrant(
            cvm_code=company.cvm_code or cvm_code,
            cnpj=cnpj,
            issuing_company=company.issuing_company,
            quotation_date=company.quotation_date,
            market=_b3_field(company, "market"),
            venue=_b3_field(company, "venue"),
            security_codes=tuple(
                OfficialSecurityCode(code=code, isin=isin)
                for code, isin in self.resolver.official_codes(company)
            ),
        )


@dataclass(frozen=True)
class _CotahistArchiveAdapter:
    """Expose the existing COTAHIST reader through the portfolio boundary."""

    archive: CotahistArchive

    async def year(self, year: int) -> Mapping[str, QuoteSeries]:
        values = await self.archive.year(year)
        return cast(Mapping[str, QuoteSeries], values)


@dataclass(frozen=True)
class _CotahistTapeAdapter:
    """Expose COTAHIST identity sessions to the targeted ingestion repair."""

    archive: CotahistArchive

    async def at(self, ticker: str, session: date) -> B3TapeObservation | None:
        code = ticker.strip().upper()
        # ``lastDatePriorEx`` is a market-session date, not necessarily a date
        # on which this illiquid security traded. One prior archive is enough to
        # bridge a year-opening event; going farther would let an unrelated old
        # listing become evidence for a gap.
        for year in range(session.year, max(1986, session.year - 1) - 1, -1):
            quotes = (await self.archive.year(year)).get(code)
            if quotes is None:
                continue
            prior = tuple(
                close for close in quotes.session_closes() if close.session <= session
            )
            if not prior:
                continue
            observed = max(prior, key=lambda close: close.session)
            identity = quotes.identity_at(observed.session)
            if identity is None:
                return None
            return B3TapeObservation(
                session=observed.session,
                isin=identity.isin,
                especi=identity.especi,
                bdi=identity.bdi,
                name=identity.name,
                code=code,
            )
        return None

    async def latest_before(
        self, ticker: str, session: date
    ) -> B3TapeObservation | None:
        code = ticker.strip().upper()
        for year in range(session.year, 1985, -1):
            quotes = (await self.archive.year(year)).get(code)
            if quotes is None:
                continue
            prior = tuple(
                close for close in quotes.session_closes() if close.session < session
            )
            if not prior:
                continue
            latest = max(prior, key=lambda close: close.session)
            identity = quotes.identity_at(latest.session)
            if identity is None:
                return None
            return B3TapeObservation(
                session=latest.session,
                isin=identity.isin,
                especi=identity.especi,
                bdi=identity.bdi,
                name=identity.name,
                code=code,
            )
        return None

    async def by_identity(
        self, session: date, *, isin: str, security_class: str
    ) -> B3TapeObservation | None:
        """Find a legacy COTAHIST code carrying the same security identity."""
        for year in range(session.year, max(1986, session.year - 1) - 1, -1):
            quotes_by_code = await self.archive.year(year)
            for code, quotes in quotes_by_code.items():
                prior = tuple(
                    close
                    for close in quotes.session_closes()
                    if close.session <= session
                )
                if not prior:
                    continue
                observed = max(prior, key=lambda close: close.session)
                identity = quotes.identity_at(observed.session)
                if identity is None or identity.isin != isin:
                    continue
                if _tape_species_class(identity.especi) != security_class:
                    continue
                return B3TapeObservation(
                    session=observed.session,
                    isin=identity.isin,
                    especi=identity.especi,
                    bdi=identity.bdi,
                    name=identity.name,
                    code=code,
                )
        return None


def _tape_species_class(value: str) -> str:
    return value.strip().upper().split(maxsplit=1)[0] if value.strip() else ""


def _b3_field(company: B3ListedCompany, field: str) -> str | None:
    """Read optional venue facts from B3's detail/supplement response."""
    for source in (company.detail, company.supplement):
        if source is None:
            continue
        value = source.get(field)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return None


async def _remember_placeholder_report(
    registry: CvmCompanyRegistry,
    collected: list[FcaPlaceholderReport] | None,
) -> None:
    if collected is None:
        return
    reader = getattr(registry, "placeholder_report", None)
    if callable(reader):
        report = await reader()
        if report not in collected:
            collected.append(report)


def _build_price_provider(
    shares_reader: SharesReader,
    archive: CotahistArchive,
    cash_events: CashEventReader,
    succession: CodeSuccession,
    unit_resolver: UnitResolver,
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
            unit_resolver=unit_resolver,
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
    fca_provenance: list[FcaSnapshotProvenance] = []
    try:
        async with httpx.AsyncClient(timeout=30.0) as http:
            artifact_store = LocalSourceArtifactStore(
                http, settings.source_artifact_dir
            )
            if whole_exchange or not tickers:
                tickers, identities = await _universe_tickers(
                    settings,
                    http,
                    fca_provenance=fca_provenance,
                    artifact_store=artifact_store,
                )
            else:
                identities = await _registry_identities(
                    settings,
                    http,
                    tickers,
                    artifact_store=artifact_store,
                    fca_provenance=fca_provenance,
                )
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
            siblings, names, historical_codes = await _security_resolvers(
                settings,
                http,
                artifact_store=artifact_store,
                snapshot=fca_provenance[0] if fca_provenance else None,
            )
            succession = CodeSuccession(
                archive,
                siblings=siblings,
                names=names,
                listed_since=_listed_since_resolver(identities),
            )
            units = _unit_resolver(identities)
            shares_reader = MongoSharesReader(
                mongo[settings.mongo_db]["raw_ingestions"],
                registrant_resolver=registrant,
                # The *candidates*, not the joined chain: a seam the price will
                # refuse is still the session an action took effect on, and this
                # is the reader that dates it (ADR 0043).
                base_changes=B3BaseChanges(archive, codes=succession.candidates),
                unit_composition_resolver=_unit_composition_resolver(identities),
                unit_resolver=units,
            )
            cash_events = MongoCashEventReader(
                mongo[settings.mongo_db]["raw_ingestions"],
                registrant_resolver=registrant,
                validation_collection=mongo[settings.mongo_db]["ingestion_validations"],
            )
            analysis_repository = SqlAlchemyAnalysisRepository(session_factory)
            use_case = AnalyzePortfolioUseCase(
                reader=MongoFundamentalsReader(
                    mongo[settings.mongo_db]["raw_ingestions"],
                    sector_resolver=_sector_resolver(identities),
                    registrant_resolver=registrant,
                    issuer_resolver=_issuer_resolver(identities),
                    per_share_resolver=_per_share_resolver(identities),
                    per_share_classes_resolver=_per_share_classes_resolver(identities),
                    per_share_rights_reason_resolver=_per_share_rights_reason_resolver(
                        identities
                    ),
                ),
                price_provider=_build_price_provider(
                    shares_reader,
                    archive,
                    cash_events,
                    succession,
                    units,
                ),
                repository=analysis_repository,
                shares_reader=shares_reader,
                classification_resolver=_classification_resolver(identities),
                classes_resolver=_classes_resolver(identities),
                class_mapping_resolver=_class_mappings_resolver(
                    identities, historical_codes
                ),
                cash_event_reader=cash_events,
                per_share_resolver=_per_share_resolver(identities),
                outcome_repository=analysis_repository,
            )
            run = await use_case.execute(tickers)
    finally:
        await mongo.close()
        await engine.dispose()

    snapshot = (
        fca_provenance[0] if fca_provenance else _default_fca_provenance(settings)
    )
    output = format_analysis(run.analyses) if verbose else format_analysis_run(run)
    print(f"{format_fca_snapshot(snapshot)}\n{output}")
    return 1 if run.failed else 0


async def _universe_tickers(
    settings: Settings,
    http: httpx.AsyncClient,
    fca_provenance: list[FcaSnapshotProvenance] | None = None,
    artifact_store: SourceArtifactStore | None = None,
    placeholder_reports: list[FcaPlaceholderReport] | None = None,
) -> tuple[tuple[str, ...], dict[str, CompanyIdentity]]:
    """Every traded code, with the identity each resolver needs (#109).

    The current universe is selected from ``cvm_fca_year``; it does not change
    when a caller selects a different accounting filing year.

    The unit here is the **ticker**, not the company the mirror is keyed on: a
    company's classes trade at different prices, so ELET3 and ELET6 have
    different multiples over the one filing they share. Analysing only the
    company's ON share would leave the other codes unanswerable — including to
    someone typing one into the search box.
    """
    registry = CvmCompanyRegistry(
        http,
        year=settings.cvm_fca_year,
        cache_dir=settings.cvm_cache_dir,
        artifact_store=artifact_store,
    )
    _configure_placeholder_recovery(registry, settings, http)
    tickers = tuple(sorted(t for c in await registry.companies() for t in c.tickers))
    identities = await registry.resolve_all(tickers)
    await _remember_placeholder_report(registry, placeholder_reports)
    if fca_provenance is not None:
        _remember_fca_provenance(fca_provenance, await registry.provenance())
    logger.info("Universe: %d traded codes", len(tickers))
    return tickers, identities


@app.command()
def doctor(
    ticker: list[str] | None = typer.Option(
        None,
        "--ticker",
        "-t",
        help="Ticker to inspect (repeatable). Default: every traded code.",
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

    Exits non-zero on any unclassified null. This is the exchange-scale coverage
    gate, not a proof that non-null arithmetic is correct (ADR 0050). With no
    flags, the command inspects the complete traded-code universe, the same
    scope selected explicitly by ``--all``.
    """
    tickers, whole_exchange = _resolve_scope(ticker, all_listed)
    exit_code = _guarded(
        _run_doctor(tickers, whole_exchange=whole_exchange, verbose=verbose)
    )
    raise typer.Exit(code=exit_code)


async def _run_doctor(
    tickers: tuple[str, ...], *, whole_exchange: bool = False, verbose: bool = False
) -> int:
    settings = get_settings()
    engine = create_engine(settings)
    session_factory = create_session_factory(engine)
    mongo = await init_database(settings)
    fca_provenance: list[FcaSnapshotProvenance] = []
    placeholder_reports: list[FcaPlaceholderReport] = []
    try:
        async with httpx.AsyncClient(timeout=30.0) as http:
            artifact_store = LocalSourceArtifactStore(
                http, settings.source_artifact_dir
            )
            if whole_exchange or not tickers:
                tickers, identities = await _universe_tickers(
                    settings,
                    http,
                    fca_provenance=fca_provenance,
                    artifact_store=artifact_store,
                    placeholder_reports=placeholder_reports,
                )
            else:
                identities = await _registry_identities(
                    settings,
                    http,
                    tickers,
                    artifact_store=artifact_store,
                    fca_provenance=fca_provenance,
                    placeholder_reports=placeholder_reports,
                )
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
                issuer_resolver=_issuer_resolver(identities),
            )
        ).execute(tickers)
    finally:
        await mongo.close()
        await engine.dispose()

    snapshot = (
        fca_provenance[0] if fca_provenance else _default_fca_provenance(settings)
    )
    if verbose:
        output = f"{format_doctor(report)}\n{format_drift(drift)}"
    else:
        output = f"{format_doctor_summary(report)}\n{format_drift_summary(drift)}"
    if placeholder_reports:
        output += "\n" + format_fca_placeholder_report(
            placeholder_reports[0], verbose=verbose
        )
    print(f"{format_fca_snapshot(snapshot)}\n{output}")
    # The coverage gate (#169, ADR 0046): every named null is a fact about the
    # world already; an unclassified one is a mapping bug or a cause nothing has
    # vocabularied yet, and at exchange scale that is the only finding a nine-
    # ticker fidelity fixture cannot see. The threshold is zero, not a share —
    # today's mirror already clears it (316,008 cells, 0 unclassified).
    return 1 if report.unclassified or report.debt_coverage.unclassified_blockers else 0


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
    What the summary must still do is name a failure or a normal no-analysis
    outcome. A skip can mean absent filings or no eligible accounting period;
    an error is ours.
    """
    counts: dict[AnalysisStatus, int] = {}
    for outcome in run.outcomes:
        counts[outcome.status] = counts.get(outcome.status, 0) + 1
    tally = ", ".join(f"{s.value}={n}" for s, n in sorted(counts.items()))

    lines: list[str] = ["", "=== Analysis run ==="]
    for outcome in run.outcomes:
        if outcome.status is AnalysisStatus.ERROR:
            lines.append(f"  !! {outcome.ticker:<8} {outcome.detail}")
    skipped = [o for o in run.outcomes if o.status is AnalysisStatus.SKIPPED]
    if skipped:
        lines.append("  skipped:")
        for outcome in sorted(skipped, key=lambda item: item.ticker):
            reason = (
                "unknown"
                if outcome.no_analysis_reason is None
                else outcome.no_analysis_reason.value
            )
            lines.append(f"    -- {outcome.ticker:<8} {reason}: {outcome.detail}")
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
    year is the normal case), and an error, quarantine, or abort is named.
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


def format_ingestion_runs(runs: Sequence[IngestionRun]) -> str:
    """Render persisted run provenance without reading terminal history."""
    lines = ["", "=== Ingestion runs ==="]
    if not runs:
        lines.append("  (no ingestion runs)")
        return "\n".join(lines)

    for run in runs:
        parameters = run.parameters
        status = run.status.value
        if run.status is IngestionRunStatus.RUNNING:
            status += " (incomplete)"
        ended = run.ended_at.isoformat() if run.ended_at is not None else "-"
        tickers = ", ".join(parameters.tickers[:8])
        if len(parameters.tickers) > 8:
            tickers += ", ..."
        parsers = ", ".join(f"{parser.name}@{parser.version}" for parser in run.parsers)
        counts = run.counts
        lines.extend(
            [
                f"  {run.run_id}  {status}",
                f"    started={run.started_at.isoformat()} ended={ended}",
                f"    scope={parameters.ticker_scope.value} "
                f"tickers={len(parameters.tickers)} [{tickers}]",
                f"    document={parameters.document} "
                f"years={','.join(str(year) for year in parameters.years)}",
                f"    modules={','.join(parameters.modules)}",
                f"    concurrency={parameters.concurrency}",
                f"    commit={run.application_commit} parsers={parsers}",
                f"    calls={counts.attempted}/{counts.planned} "
                f"excluded={counts.excluded} remaining={counts.remaining} "
                f"stored={counts.stored} "
                f"unchanged={counts.unchanged} "
                f"skipped={counts.skipped} error={counts.error} "
                f"quarantined={counts.quarantined} "
                f"aborted={counts.aborted}",
                _format_metrics_summary(run),
            ]
        )
        if run.failure is not None:
            lines.append(f"    failure={run.failure}")
    return "\n".join(lines)


def format_ingestion_metrics(run: IngestionRun) -> str:
    """Render a run's persisted timing and volume measurements."""
    return "\n".join(
        [
            "",
            "=== Ingestion metrics ===",
            f"  {run.run_id}  {run.status.value}",
            _format_metrics_summary(run),
        ]
    )


def _format_metrics_summary(run: IngestionRun) -> str:
    metrics = run.metrics
    elapsed = _elapsed_seconds(run)
    elapsed_text = f"{elapsed:.3f}s" if elapsed is not None else "-"
    throughput = (
        f"{run.counts.attempted / elapsed:.2f} calls/s"
        if elapsed is not None and elapsed > 0
        else "-"
    )
    return (
        f"    metrics elapsed={elapsed_text} throughput={throughput} "
        f"source={metrics.source_seconds:.3f}s "
        f"download={metrics.download_seconds:.3f}s parse={metrics.parse_seconds:.3f}s "
        f"store={metrics.store_seconds:.3f}s "
        f"retry_wait={metrics.retry_wait_seconds:.3f}s rows={metrics.rows} "
        f"payload_bytes={metrics.payload_bytes} archive_bytes={metrics.archive_bytes} "
        f"cache_hit={metrics.cache_hits} cache_miss={metrics.cache_misses}"
    )


def _metrics_log_event(run: IngestionRun) -> dict[str, object]:
    metrics = run.metrics
    elapsed = _elapsed_seconds(run)
    return {
        "event": "ingestion.run",
        "run_id": run.run_id,
        "status": run.status.value,
        "elapsed_seconds": elapsed,
        "throughput_calls_per_second": (
            run.counts.attempted / elapsed
            if elapsed is not None and elapsed > 0
            else None
        ),
        "source_seconds": metrics.source_seconds,
        "download_seconds": metrics.download_seconds,
        "parse_seconds": metrics.parse_seconds,
        "store_seconds": metrics.store_seconds,
        "retry_wait_seconds": metrics.retry_wait_seconds,
        "rows": metrics.rows,
        "payload_bytes": metrics.payload_bytes,
        "archive_bytes": metrics.archive_bytes,
        "cache_hits": metrics.cache_hits,
        "cache_misses": metrics.cache_misses,
    }


def _elapsed_seconds(run: IngestionRun) -> float | None:
    if run.ended_at is None:
        return None
    return (run.ended_at - run.started_at).total_seconds()


def format_ingestion_validations(
    reports: Sequence[IngestionValidationReport],
) -> str:
    """Render durable batch validation evidence and the replay/approval workflow."""
    lines = ["", "=== Ingestion validations ==="]
    if not reports:
        lines.append("  (no validation reports)")
        return "\n".join(lines)
    for report in reports:
        validation = report.validation
        rules = ", ".join(f"{rule.name}@{rule.version}" for rule in validation.rules)
        lines.append(f"  {report.report_id}  {report.status.value} run={report.run_id}")
        lines.append(
            f"    {validation.source}:{validation.batch} "
            f"parser={validation.parser.name}@{validation.parser.version}"
        )
        lines.append(f"    artifact={validation.artifact_id or '-'} rules={rules}")
        _append_validation_reconciliation(lines, validation.observations)
        if validation.findings:
            lines.extend(
                f"    !! {finding.code}: {finding.detail}"
                for finding in validation.findings
            )
        _append_validation_evidence(lines, validation.evidence)
        if report.approval_note is not None:
            lines.append(f"    approval={report.approval_note}")
    lines.append(
        "--- Approval records review only. After a parser/rule version bump, rerun "
        "the same ingest command with --force; an unchanged CVM URL reuses its "
        "SHA-256 archive."
    )
    return "\n".join(lines)


def _append_validation_reconciliation(
    lines: list[str], observations: Mapping[str, str | int | bool]
) -> None:
    """Render row-level source reconciliation when a validation records it."""
    fields = ("fetched", "accepted", "rejected", "deduplicated")
    if not all(field in observations for field in fields):
        return
    values = " ".join(f"{field}={observations[field]}" for field in fields)
    coverage = observations.get("coverage_established")
    lines.append(f"    reconciliation={values} coverage_established={coverage}")


def _append_validation_evidence(
    lines: list[str], evidence: Mapping[str, object]
) -> None:
    """Name retained row evidence without printing a potentially large payload."""
    for key in ("rejected_rows", "deduplicated_rows"):
        value = evidence.get(key)
        if isinstance(value, list):
            lines.append(f"    evidence={key} count={len(value)}")


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
            f"  P/L básico {_num(i.pe_basic)}  P/VP {_num(i.pb)}"
            f"  EV/EBITDA {_num(i.ev_ebitda)}"
            f"  DY {_pct(i.dividend_yield)}"
        )
        lines.append(
            f"  net debt/EBITDA {_num(i.net_debt_to_ebitda)}"
            f"  current {_num(i.current_ratio)}"
            f"  rev growth {_pct(i.revenue_growth)}"
            f"  NI growth {_pct(i.net_income_growth)}"
        )
    return "\n".join(lines)


def format_fca_snapshot(provenance: FcaSnapshotProvenance) -> str:
    """Render the FCA identity snapshot used by a command."""
    return (
        "=== FCA identity snapshot ===\n"
        f"  FCA snapshot year={provenance.year} source={provenance.source} "
        f"url={provenance.source_url} artifact={provenance.artifact_id or '-'}"
    )


def _format_exercise(exercise: ExerciseCoverage) -> list[str]:
    """One header line per exercise, then a line per null cell with its cause."""
    total = len(exercise.indicators)
    price_source = (
        f" | price_source=B3:{exercise.price_source_code}"
        f"@{exercise.price_source_session}"
        if exercise.price_source_code is not None
        and exercise.price_source_session is not None
        else " | price_source=unavailable"
    )
    header = (
        f"  {exercise.view:<11} ref {exercise.reference_date} "
        f"| {exercise.values}/{total} values, "
        f"named {exercise.named_nulls}, unclassified {exercise.unclassified}"
        f"{price_source}"
    )
    lines = [header]
    for cell in exercise.indicators:
        if cell.has_value:
            continue
        mark = "!!" if cell.is_unclassified else "  "
        lines.append(f"    {mark} {cell.indicator:<26} {cell.status}")
    provenance = exercise.cpc41_window_provenance
    if provenance is not None:
        periods = ",".join(
            f"{period.reference_date}:"
            f"{period.basic_weighted_shares_status.value}/"
            f"{period.diluted_weighted_shares_status.value}"
            for period in provenance.selected_periods
        )
        lines.append(
            "    cpc41 window: "
            f"periods={periods or 'none'} "
            f"basic_blocker={provenance.basic_blocker or 'none'} "
            f"diluted_blocker={provenance.diluted_blocker or 'none'}"
        )
        for period in provenance.selected_periods:
            basic_disclosure = (
                period.basic_disclosure_status or period.disclosure_status
            )
            diluted_disclosure = (
                period.diluted_disclosure_status or period.disclosure_status
            )
            basic_class = period.basic_class_status or period.class_status
            diluted_class = period.diluted_class_status or period.class_status
            basic_multiplier = (
                period.basic_multiplier_status or period.multiplier_status
            )
            diluted_multiplier = (
                period.diluted_multiplier_status or period.multiplier_status
            )
            lines.append(
                f"      cpc41 period={period.reference_date} "
                f"basic={basic_disclosure.value}/{basic_class.value}/"
                f"{basic_multiplier.value}/{period.basic_weighted_shares_status.value} "
                f"blocker={period.basic_blocker or 'none'} "
                f"diluted={diluted_disclosure.value}/{diluted_class.value}/"
                f"{diluted_multiplier.value}/"
                f"{period.diluted_weighted_shares_status.value} "
                f"blocker={period.diluted_blocker or 'none'}"
            )
            if period.source_accounts:
                for account in period.source_accounts:
                    lines.append(
                        "        cpc41 raw_ref "
                        f"basis={account.basis or 'unknown'} "
                        f"module={account.module} code={account.code} "
                        f"name={account.name!r} "
                        f"selection={account.selection_status.value} "
                        f"expected={str(account.expected).lower()}"
                    )
            else:
                lines.append("        cpc41 raw_ref none")
    return lines


def format_doctor(report: DoctorReport) -> str:
    """Render the coverage report and a status tally over every cell (#47)."""
    lines: list[str] = ["", "=== smaug doctor — persisted analysis coverage ==="]
    named: dict[NullReason, int] = {}
    exercises = 0
    tickers_with_unclassified: set[str] = set()

    for ticker_cov in report.tickers:
        lines.append(f"\n{ticker_cov.ticker} [{ticker_cov.sector.value}]")
        outcome = _format_no_analysis_outcome(ticker_cov)
        if outcome is not None:
            lines.append(f"  no-analysis outcome: {outcome}")
        if not ticker_cov.exercises:
            lines.append("  !! (no persisted analysis)")
            continue
        for exercise in ticker_cov.exercises:
            exercises += 1
            lines.extend(_format_exercise(exercise))
            for cell in exercise.indicators:
                if not cell.has_value and cell.reason is not None:
                    named[cell.reason] = named.get(cell.reason, 0) + 1
                elif cell.is_unclassified:
                    tickers_with_unclassified.add(ticker_cov.ticker)

    totals = report.totals
    lines.append("")
    lines.append(
        f"--- {len(report.tickers)} tickers, {exercises} exercises, "
        f"{totals.total_cells} cells "
        f"| value={totals.values} named={totals.named_nulls} "
        f"unclassified={totals.unclassified}"
    )
    lines.extend(_format_coverage_details(report))
    if named:
        breakdown = ", ".join(
            f"{reason.value}={n}" for reason, n in sorted(named.items())
        )
        lines.append(f"    named breakdown: {breakdown}")
    if totals.unclassified:
        who = ", ".join(sorted(tickers_with_unclassified))
        lines.append(f"    !! {totals.unclassified} unclassified nulls across: {who}")
    lines.extend(_format_debt_coverage(report.debt_coverage))
    return "\n".join(lines)


def _format_debt_coverage(summary: DebtCoverageSummary) -> list[str]:
    """Render the one-row debt decision count beside dependent cell counts."""
    views = ",".join(summary.views)
    return [
        "",
        "--- debt coverage evidence ---",
        f"    universe={summary.universe}; views={views}",
        f"    persisted decisions={summary.persisted_decisions} "
        f"incomplete={summary.incomplete_decisions} "
        f"inapplicable={summary.inapplicable_decisions}",
        f"    dependent indicator cells with incomplete_debt_coverage="
        f"{summary.incomplete_indicator_cells}",
        f"    legacy snapshots={summary.legacy_snapshots} "
        f"unclassified blockers={summary.unclassified_blockers}",
        f"    period={summary.period_definition}; cell={summary.cell_definition}",
    ]


def _format_coverage_details(report: DoctorReport) -> list[str]:
    """Render scope, disposition totals, and explicit percentage denominators."""
    scope = report.coverage_scope
    totals = report.totals
    return [
        (
            "--- scope --- "
            f"requested={scope.requested_tickers} "
            f"persisted={scope.persisted_tickers} "
            f"no-analysis={scope.no_analysis_tickers} "
            f"stale={scope.stale_rows} legacy={scope.legacy_rows} "
            f"stored_rows={scope.stored_rows} "
            "| "
            f"requested_tickers={scope.requested_tickers} "
            f"persisted_tickers={scope.persisted_tickers} "
            f"no_analysis_tickers={scope.no_analysis_tickers} "
            f"persisted_exercises={scope.persisted_exercises} "
            f"stored_rows={scope.stored_rows} "
            f"stale_rows={scope.stale_rows} legacy_rows={scope.legacy_rows}"
        ),
        (
            "    cells: "
            f"total={totals.total_cells} values={totals.values} "
            f"nulls={totals.nulls} named={totals.named_nulls} "
            f"unclassified={totals.unclassified}"
        ),
        (
            "    dispositions: "
            f"inapplicable={totals.inapplicable} "
            f"mathematically_undefined={totals.mathematically_undefined} "
            f"primary_source_unavailable={totals.primary_source_unavailable} "
            f"recoverable_gap={totals.recoverable_gap} "
            "historical_period_does_not_exist="
            f"{totals.historical_period_does_not_exist}"
        ),
        (
            "    percentages (denominator=null indicator cells "
            f"({totals.nulls}) / all indicator cells ({totals.total_cells})): "
            f"missing_or_recoverable={totals.missing_or_recoverable} "
            f"({totals.missing_or_recoverable_pct_of_nulls:.1f}% of nulls; "
            f"{totals.missing_or_recoverable_pct_of_cells:.1f}% of all cells); "
            f"lower_bound={totals.missing_or_recoverable_lower_bound}; "
            f"upper_bound={totals.missing_or_recoverable_upper_bound} "
            f"({totals.missing_or_recoverable_upper_pct_of_nulls:.1f}% of nulls; "
            f"{totals.missing_or_recoverable_upper_pct_of_cells:.1f}% of all cells); "
            f"mixed_comparability={totals.mixed_comparability}; "
            f"genuine_inapplicability={totals.inapplicable} "
            f"({totals.inapplicable_pct_of_nulls:.1f}% of nulls; "
            f"{totals.inapplicable_pct_of_cells:.1f}% of all cells)"
        ),
    ]


def _format_no_analysis_outcome(ticker: TickerCoverage) -> str | None:
    """Render only a named skip from the latest run, never a stale reason."""
    outcome: AnalysisOutcome | None = ticker.outcome
    if (
        outcome is None
        or outcome.status is not AnalysisStatus.SKIPPED
        or outcome.no_analysis_reason is None
    ):
        return None
    return (
        f"ticker={ticker.ticker} status={outcome.status.value} "
        f"reason={outcome.no_analysis_reason.value} detail={outcome.detail}"
    )


def format_doctor_summary(report: DoctorReport) -> str:
    """The coverage report as totals — the M0 gate at exchange scale.

    Same numbers as the per-cell listing, minus the cells. The one thing it does
    not compress is an **unclassified** null: a value we do not have and cannot
    explain is the only finding here that asks for work, so every ticker holding
    one is named. A named null is a fact about the world already, and a count of
    it is enough.
    """
    named: dict[NullReason, int] = {}
    exercises = price_provenance = 0
    unnamed_tickers: set[str] = set()
    empty: list[str] = []
    no_analysis_outcomes: list[str] = []

    for ticker_cov in report.tickers:
        outcome = _format_no_analysis_outcome(ticker_cov)
        if outcome is not None:
            no_analysis_outcomes.append(outcome)
        if not ticker_cov.exercises:
            empty.append(ticker_cov.ticker)
            continue
        for exercise in ticker_cov.exercises:
            exercises += 1
            if (
                exercise.price_source_code is not None
                and exercise.price_source_session is not None
            ):
                price_provenance += 1
            for cell in exercise.indicators:
                if not cell.has_value and cell.reason is not None:
                    named[cell.reason] = named.get(cell.reason, 0) + 1
                elif cell.is_unclassified:
                    unnamed_tickers.add(ticker_cov.ticker)

    totals = report.totals
    analyzed = report.coverage_scope.persisted_tickers
    share = totals.percentage_of_cells(totals.values)
    lines: list[str] = [
        "",
        "=== smaug doctor — persisted analysis coverage ===",
        f"  {analyzed} of {len(report.tickers)} ticker(s) analyzed, "
        f"{exercises} exercises, {totals.total_cells} cells",
        f"  value={totals.values} ({share:.1f}%) named={totals.named_nulls} "
        f"unclassified={totals.unclassified}",
        f"  price provenance={price_provenance}/{exercises} exercises "
        "(B3 code + session)",
    ]
    lines.extend(_format_coverage_details(report))
    if no_analysis_outcomes:
        lines.append("  latest no-analysis outcomes:")
        lines.extend(f"    {outcome}" for outcome in sorted(no_analysis_outcomes))
    for reason, count in sorted(named.items(), key=lambda kv: -kv[1]):
        lines.append(f"    {reason.value:<26} {count:>7}")
    if empty:
        shown = ", ".join(sorted(empty)[:12])
        more = f" (+{len(empty) - 12} more)" if len(empty) > 12 else ""
        lines.append(f"  no persisted analysis: {shown}{more}")
    if totals.unclassified:
        who = ", ".join(sorted(unnamed_tickers))
        lines.append(f"    !! {totals.unclassified} unclassified nulls across: {who}")
    else:
        lines.append("    every null carries a named cause.")
    lines.extend(_format_debt_coverage(report.debt_coverage))
    return "\n".join(lines)


def format_fca_placeholder_report(
    report: FcaPlaceholderReport, *, verbose: bool = False
) -> str:
    """Render the reproducible FCA placeholder inventory for ``doctor``."""
    issues: dict[str, int] = {}
    reasons: dict[str, int] = {}
    for finding in report.findings:
        issue = finding.row.code_issue.value
        issues[issue] = issues.get(issue, 0) + 1
        reasons[finding.reason] = reasons.get(finding.reason, 0) + 1
    issue_text = (
        ", ".join(f"{name}={count}" for name, count in sorted(issues.items())) or "none"
    )
    lines = [
        "",
        "=== smaug doctor — FCA placeholder ticker recovery ===",
        (
            f"  snapshot={report.snapshot_year} rows={report.total} "
            f"recovered={report.recovered_count} "
            f"unresolved={report.unresolved_count}"
        ),
        f"  inventory: {issue_text}",
    ]
    recovered = report.recovered
    if recovered:
        lines.append("  recovered:")
        for finding in recovered:
            lines.append(_format_placeholder_finding(finding))
    if verbose:
        unresolved = report.unresolved
        if unresolved:
            lines.append("  unresolved:")
            lines.extend(_format_placeholder_finding(finding) for finding in unresolved)
    else:
        reason_text = (
            ", ".join(f"{name}={count}" for name, count in sorted(reasons.items()))
            or "none"
        )
        lines.append(f"  outcomes: {reason_text}")
    return "\n".join(lines)


def _format_placeholder_finding(finding: FcaPlaceholderFinding) -> str:
    """Render one finding without allowing names to become recovery evidence."""
    row = finding.row
    candidates = ",".join(finding.candidate_codes) or "-"
    observed = ",".join(finding.observed_codes) or "-"
    recovered = ",".join(finding.recovered_codes) or "-"
    window = f"{finding.window_start or '-'}..{finding.window_end or '-'}"
    suffix = f" detail={finding.detail}" if finding.detail else ""
    return (
        f"    row={row.row_number} cnpj={row.cnpj or '-'} "
        f"cd_cvm={row.cd_cvm or '-'} raw={row.raw_code or '<blank>'} "
        f"{finding.status.value} reason={finding.reason} "
        f"candidates={candidates} observed={observed} recovered={recovered} "
        f"root={finding.official_root or '-'} window={window}{suffix}"
    )


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
