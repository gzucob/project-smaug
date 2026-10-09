"""FastAPI read API for the computed indicators, plus the portfolio's write
surface (Phase 2 delivery).

Serves the latest persisted analysis per ticker as JSON — the surface the
front-end consumes. This is the composition root for the API: it wires the
Postgres repositories and maps domain entities to Pydantic response models.
Computation/persistence of *analysis* stays the ``analyze`` CLI command's job
(``AGENTS.md``'s "the API is a read API, not a write one" — still true for
indicators); the portfolio (which tickers the user favorited, #151) is the one
thing this API is allowed to write, since it is not computed, only chosen.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import date, datetime
from decimal import Decimal
from typing import Any, Literal

from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from pymongo import AsyncMongoClient

from smaug.analysis.application.price_history import ReadPriceHistoryUseCase
from smaug.analysis.domain.entities import VIEW_TTM, TickerAnalysis
from smaug.analysis.domain.financials import (
    AccountingRegime,
    B3CapitalEventReconciliation,
    CapitalActionEvidence,
    CapitalComposition,
    ClassMarketValue,
    Cpc41EvidenceStatus,
    Cpc41PeriodProvenance,
    Cpc41SelectionStatus,
    Cpc41WindowProvenance,
    DebtBlocker,
    DebtEvidenceSnapshot,
    DebtIdentityStatus,
    DebtInstrument,
    DebtLineClassification,
    DebtLineEvidence,
    DebtLineRole,
    RegimeSource,
    ShareCountProvenance,
    ShareCounts,
    SourceAccountStatus,
)
from smaug.analysis.domain.indicators import (
    NullReason,
    indicator_contracts,
    is_retired_sector_input,
    public_indicator_names,
)
from smaug.analysis.domain.price_history import PriceHistory
from smaug.analysis.infrastructure.mongo_dividends import MongoCashEventReader
from smaug.analysis.infrastructure.sql_price_history import SqlPriceHistoryRepository
from smaug.analysis.infrastructure.sql_repository import SqlAlchemyAnalysisRepository
from smaug.portfolio.application.manage_portfolio import ManagePortfolioUseCase
from smaug.portfolio.domain.entities import PortfolioTicker
from smaug.portfolio.domain.share_classes import (
    EconomicRightsStatus,
    ShareClassMapping,
    ShareClassMappingStatus,
)
from smaug.portfolio.infrastructure.sql_repository import SqlAlchemyPortfolioRepository
from smaug.shared.config import get_settings
from smaug.shared.errors import UnknownTickerError
from smaug.shared.sql_db import create_engine, create_session_factory

_settings = get_settings()
_session_factory = create_session_factory(create_engine(_settings))
_repository = SqlAlchemyAnalysisRepository(_session_factory)
_price_history = ReadPriceHistoryUseCase(SqlPriceHistoryRepository(_session_factory))
_portfolio = ManagePortfolioUseCase(SqlAlchemyPortfolioRepository(_session_factory))


@asynccontextmanager
async def _lifespan(_app: FastAPI) -> AsyncIterator[None]:
    """Open the raw mirror for read-only event history requests."""
    client: AsyncMongoClient[dict[str, Any]] = AsyncMongoClient(_settings.mongo_uri)
    _app.state.mongo_database = client[_settings.mongo_db]
    try:
        yield
    finally:
        await client.close()


app = FastAPI(
    title="smaug — análise fundamentalista", version="0.1.0", lifespan=_lifespan
)
# The only cross-origin caller is PR 2's Next.js Route Handler proxying the
# favorite-ticker toggle — every read stays server-side (RULES_FRONTEND), so
# this never needs to admit a browser origin, only that one server.
app.add_middleware(
    CORSMiddleware,
    allow_origins=list(_settings.api_cors_origins),
    allow_methods=["GET", "POST", "DELETE"],
    allow_headers=["*"],
)


class TickerCodeEvidenceResponse(BaseModel):
    """One CVM-filed code and its FCA years."""

    symbol: str
    filed_years: list[int]
    source: str


class ShareClassMappingResponse(BaseModel):
    """Stable class identity, economic-right status, and source evidence."""

    class_id: str
    symbol: str | None
    kind: str | None
    per_share_class: str | None
    status: str
    economic_rights: str
    resolution_reason: str | None
    code_evidence: list[TickerCodeEvidenceResponse]
    evidence: list[str]


class ClassMarketValueResponse(BaseModel):
    """The class-level contribution to the persisted market cap."""

    class_id: str
    symbol: str
    per_share_class: str
    price: Decimal | None
    shares: Decimal | None
    value: Decimal | None
    price_basis: str
    share_basis: str
    null_reason: NullReason | None


class ShareCountsResponse(BaseModel):
    """Filed or restated class counts retained as capital evidence."""

    common: Decimal | None
    preferred: Decimal | None
    total: Decimal | None
    preferred_a: Decimal | None
    preferred_b: Decimal | None
    preferred_other: Decimal | None


class CapitalCompositionResponse(BaseModel):
    """CVM statement composition that names treasury shares."""

    issued_total: Decimal | None
    treasury_common: Decimal | None
    treasury_preferred: Decimal | None
    treasury_total: Decimal | None


class CapitalActionResponse(BaseModel):
    """Class-aware CVM capital event retained as provenance."""

    approval_date: str
    kind: str
    common_before: Decimal | None
    common_after: Decimal | None
    preferred_before: Decimal | None
    preferred_after: Decimal | None
    total_before: Decimal | None
    total_after: Decimal | None


class B3CapitalEventEvidenceResponse(BaseModel):
    """One B3 stock-event row retained for restatement audit."""

    status: str
    approval_date: str
    kind: str
    factor: str
    last_date_prior: str
    isin_code: str
    asset_issued: str
    remarks: str


class B3CapitalEventReconciliationResponse(BaseModel):
    """B3 stock-event row counts and amendment evidence."""

    fetched: int
    accepted: int
    rejected: int
    deduplicated: int
    conflicting: int
    rows: list[B3CapitalEventEvidenceResponse]


class ShareCountProvenanceResponse(BaseModel):
    """Audit trail behind one outstanding-share reading."""

    requested_year: int
    filed_year: int | None
    status: str
    source: str
    issued: ShareCountsResponse | None
    outstanding: ShareCountsResponse | None
    treasury: CapitalCompositionResponse | None
    restatement_factor: Decimal | None
    actions: list[CapitalActionResponse]
    evidence: list[str]
    b3_reconciliation: B3CapitalEventReconciliationResponse | None


class Cpc41AccountEvidenceResponse(BaseModel):
    """One raw CVM account considered for a selected CPC 41 period."""

    module: str
    code: str
    name: str
    selection_status: Cpc41SelectionStatus
    value: Decimal | None
    basis: str | None
    expected: bool = False


class Cpc41PeriodProvenanceResponse(BaseModel):
    """Filed CPC 41 evidence for one period in the arithmetic TTM window."""

    reference_date: date
    disclosure_status: Cpc41EvidenceStatus
    class_status: Cpc41EvidenceStatus
    multiplier_status: Cpc41EvidenceStatus
    multiplier: Decimal | None
    basic_weighted_shares: Decimal | None
    basic_weighted_shares_status: Cpc41EvidenceStatus
    diluted_weighted_shares: Decimal | None
    diluted_weighted_shares_status: Cpc41EvidenceStatus
    basic_blocker: NullReason | None
    diluted_blocker: NullReason | None
    source_accounts: list[Cpc41AccountEvidenceResponse]
    basic_disclosure_status: Cpc41EvidenceStatus | None = None
    diluted_disclosure_status: Cpc41EvidenceStatus | None = None
    basic_class_status: Cpc41EvidenceStatus | None = None
    diluted_class_status: Cpc41EvidenceStatus | None = None
    basic_multiplier_status: Cpc41EvidenceStatus | None = None
    diluted_multiplier_status: Cpc41EvidenceStatus | None = None


class Cpc41WindowProvenanceResponse(BaseModel):
    """The selected four-period CPC 41 lineage and its strict blockers."""

    selected_periods: list[Cpc41PeriodProvenanceResponse]
    basic_blocker: NullReason | None
    diluted_blocker: NullReason | None


class IndicatorsResponse(BaseModel):
    """The computed indicators.

    ``null_reasons`` names why each null field is null using ``NullReason``'s
    enumerable vocabulary, keyed by the field's name. A null field with no
    entry is unclassified.
    """

    roe: Decimal | None
    roe_total: Decimal | None
    roa: Decimal | None
    roa_total: Decimal | None
    roic_statutory: Decimal | None
    net_margin: Decimal | None
    net_margin_total: Decimal | None
    gross_margin: Decimal | None
    ebit_margin: Decimal | None
    ebitda_margin: Decimal | None
    asset_turnover: Decimal | None
    eps_basic: Decimal | None
    eps_diluted: Decimal | None
    bvps: Decimal | None
    net_debt: Decimal | None
    cash_equivalents: Decimal | None
    current_financial_investments: Decimal | None
    net_debt_to_ebitda: Decimal | None
    net_debt_to_ebit: Decimal | None
    net_debt_to_equity: Decimal | None
    debt_to_equity: Decimal | None
    liabilities_to_assets: Decimal | None
    equity_to_assets: Decimal | None
    current_ratio: Decimal | None
    price_to_cfo: Decimal | None
    ev_cfo: Decimal | None
    ev_fcf: Decimal | None
    cash_ratio: Decimal | None
    quick_ratio: Decimal | None
    ev_revenue: Decimal | None
    tag_along: Decimal | None
    free_float: Decimal | None
    price_to_ebitda: Decimal | None
    cfo_yield: Decimal | None
    cfo_margin: Decimal | None
    fcf_margin: Decimal | None
    cash_conversion: Decimal | None
    capex_to_cfo: Decimal | None
    revenue_growth: Decimal | None
    net_income_growth: Decimal | None
    revenue_cagr_5y: Decimal | None
    ebitda_cagr_5y: Decimal | None
    ebit_cagr_5y: Decimal | None
    net_income_cagr_5y: Decimal | None
    earnings_yield: Decimal | None
    pe_basic: Decimal | None
    pe_diluted: Decimal | None
    pb: Decimal | None
    psr: Decimal | None
    price_to_assets: Decimal | None
    price_to_ebit: Decimal | None
    price_to_working_capital: Decimal | None
    dividend_yield: Decimal | None
    payout_cash_paid_in_period: Decimal | None
    ev_ebitda: Decimal | None
    ev_ebit: Decimal | None
    fcf: Decimal | None
    price_to_fcf: Decimal | None
    fcf_yield: Decimal | None
    revenue: Decimal | None
    costs: Decimal | None
    net_income: Decimal | None
    net_income_total: Decimal | None
    distributions_per_security: Decimal | None
    company_distributions_paid_in_period: Decimal | None
    total_assets: Decimal | None
    total_liabilities: Decimal | None
    current_assets: Decimal | None
    noncurrent_assets: Decimal | None
    current_liabilities: Decimal | None
    noncurrent_liabilities: Decimal | None
    equity: Decimal | None
    equity_total: Decimal | None
    market_cap: Decimal | None
    enterprise_value: Decimal | None
    non_controlling_interests: Decimal | None
    shares: Decimal | None
    null_reasons: dict[str, str]
    source_account_evidence: list[SourceAccountEvidenceResponse]
    cpc41_window_provenance: Cpc41WindowProvenanceResponse | None


class IndicatorContractResponse(BaseModel):
    """Formula and provenance metadata for one market-facing indicator."""

    basis: str
    numerator: str
    denominator: str
    reference_period: str
    price_basis: str
    share_basis: str
    provenance: list[str]


class ClassificationResponse(BaseModel):
    """The B3 economic taxonomy: setor → subsetor → segmento (ADR 0024)."""

    setor: str
    subsetor: str | None
    segmento: str | None


class SourceAccountRefResponse(BaseModel):
    """One raw account retained in source-account provenance."""

    code: str
    name: str
    value: Decimal | None
    column: str | None = None


class IncomeStatementResponse(BaseModel):
    """Selected DRE line items for the analysis period, in absolute reais."""

    revenue: Decimal | None
    costs: Decimal | None
    gross_profit: Decimal | None
    operating_expenses: Decimal | None
    ebitda: Decimal | None
    dep_amort: Decimal | None
    ebit: Decimal | None
    income_tax_expense: Decimal | None
    net_income_total: Decimal | None


class SourceAccountEvidenceResponse(BaseModel):
    """Mapping/absence evidence for one calculator input."""

    field: str
    statement: str
    status: SourceAccountStatus
    expected: list[str]
    found: list[SourceAccountRefResponse]
    parent_code: str | None
    formula: str | None
    dependencies: list[str]
    blocker: NullReason | None
    consumer_indicators: list[str]
    duplicates_discarded: int = 0


class DebtLineResponse(BaseModel):
    """A selected or relevant excluded line from the filing's BPP."""

    code: str
    name: str
    value: Decimal | None
    role: DebtLineRole
    reason: DebtBlocker | None
    instrument: DebtInstrument
    classification: DebtLineClassification


class DebtEvidenceResponse(BaseModel):
    """Raw-BPP evidence behind one persisted debt decision."""

    regime: AccountingRegime
    regime_source: RegimeSource
    identity_status: DebtIdentityStatus
    used_lines: list[DebtLineResponse]
    excluded_lines: list[DebtLineResponse]
    included_instruments: list[str]
    primary_blocker: DebtBlocker | None
    secondary_blockers: list[DebtBlocker]


class GovernanceResponse(BaseModel):
    """IPO date, dated listing segment, and selected tag-along evidence."""

    ipo_date: date | None
    listing_segment: str | None
    listing_observed_on: date | None
    listing_source: str | None
    tag_along_source: str | None
    tag_along_reference: str | None
    blocker: str | None


class AnalysisResponse(BaseModel):
    """One ticker's analysis for a single view: provenance + indicator contract."""

    ticker: str
    view: str
    classification: ClassificationResponse
    governance: GovernanceResponse
    reference_date: date
    computed_at: datetime
    calculation_contract_version: str
    filed_regime: AccountingRegime | None
    regime_source: RegimeSource | None
    issuer: str | None
    cd_cvm: str | None
    cnpj: str | None
    price: Decimal | None
    price_source_code: str | None
    price_source_session: date | None
    price_adjusted: Decimal | None
    price_basis: str | None
    share_count_basis: str | None
    liquidity_basis: str | None
    debt_basis: str | None
    debt_evidence_snapshot: DebtEvidenceSnapshot | None
    debt_evidence: DebtEvidenceResponse | None
    roic_tax_basis: str | None
    share_class_mappings: list[ShareClassMappingResponse]
    class_market_values: list[ClassMarketValueResponse]
    capital_provenance: ShareCountProvenanceResponse | None
    income_statement: IncomeStatementResponse | None
    cash_flow_statement: list[SourceAccountRefResponse]
    indicators: IndicatorsResponse
    indicator_contract: dict[str, IndicatorContractResponse]


class TickerViewsResponse(BaseModel):
    """Both perspectives for one ticker: the live TTM plus the closed-year history."""

    ticker: str
    ttm: AnalysisResponse | None
    history: list[AnalysisResponse]  # closed years, oldest → newest


class PortfolioTickerResponse(BaseModel):
    """One favorited ticker (#151)."""

    ticker: str
    added_at: datetime


def _to_portfolio_response(entry: PortfolioTicker) -> PortfolioTickerResponse:
    return PortfolioTickerResponse(ticker=entry.ticker, added_at=entry.added_at)


def _to_indicator_contract(
    analysis: TickerAnalysis,
) -> dict[str, IndicatorContractResponse]:
    """Resolve static formula metadata against the row's view."""
    period = "last_twelve_months" if analysis.view == VIEW_TTM else "closed_fiscal_year"
    return {
        key: IndicatorContractResponse(
            basis=contract.basis,
            numerator=contract.numerator,
            denominator=contract.denominator,
            reference_period=(
                period
                if contract.reference_period == "view_period"
                else contract.reference_period
            ),
            price_basis=contract.price_basis,
            share_basis=contract.share_basis,
            provenance=list(contract.provenance),
        )
        for key, contract in indicator_contracts(
            analysis.calculation_contract_version
        ).items()
        if key in public_indicator_names()
    }


def _share_counts_response(counts: ShareCounts | None) -> ShareCountsResponse | None:
    if counts is None:
        return None
    return ShareCountsResponse(
        common=counts.common,
        preferred=counts.preferred,
        total=counts.total,
        preferred_a=counts.preferred_a,
        preferred_b=counts.preferred_b,
        preferred_other=counts.preferred_other,
    )


def _capital_composition_response(
    composition: CapitalComposition | None,
) -> CapitalCompositionResponse | None:
    if composition is None:
        return None
    return CapitalCompositionResponse(
        issued_total=composition.issued_total,
        treasury_common=composition.treasury_common,
        treasury_preferred=composition.treasury_preferred,
        treasury_total=composition.treasury_total,
    )


def _mapping_response(mapping: ShareClassMapping) -> ShareClassMappingResponse:
    return ShareClassMappingResponse(
        class_id=mapping.class_id,
        symbol=mapping.symbol,
        kind=None if mapping.kind is None else mapping.kind.value,
        per_share_class=(
            None if mapping.per_share_class is None else mapping.per_share_class.value
        ),
        status=mapping.status.value,
        economic_rights=mapping.economic_rights.value,
        resolution_reason=(
            None
            if mapping.resolution_reason is None
            else mapping.resolution_reason.value
        ),
        code_evidence=[
            TickerCodeEvidenceResponse(
                symbol=evidence.symbol,
                filed_years=list(evidence.filed_years),
                source=evidence.source,
            )
            for evidence in mapping.code_evidence
        ],
        evidence=list(mapping.evidence),
    )


def _class_market_value_response(
    value: ClassMarketValue,
) -> ClassMarketValueResponse:
    return ClassMarketValueResponse(
        class_id=value.class_id,
        symbol=value.symbol,
        per_share_class=value.per_share_class.value,
        price=value.price,
        shares=value.shares,
        value=value.value,
        price_basis=value.price_basis,
        share_basis=value.share_basis,
        null_reason=value.null_reason,
    )


def _capital_provenance_response(
    provenance: ShareCountProvenance | None,
) -> ShareCountProvenanceResponse | None:
    if provenance is None:
        return None

    def action_response(action: CapitalActionEvidence) -> CapitalActionResponse:
        return CapitalActionResponse(
            approval_date=action.approval_date,
            kind=action.kind,
            common_before=action.common_before,
            common_after=action.common_after,
            preferred_before=action.preferred_before,
            preferred_after=action.preferred_after,
            total_before=action.total_before,
            total_after=action.total_after,
        )

    def b3_response(
        reconciliation: B3CapitalEventReconciliation | None,
    ) -> B3CapitalEventReconciliationResponse | None:
        if reconciliation is None:
            return None
        return B3CapitalEventReconciliationResponse(
            fetched=reconciliation.fetched,
            accepted=reconciliation.accepted,
            rejected=reconciliation.rejected,
            deduplicated=reconciliation.deduplicated,
            conflicting=reconciliation.conflicting,
            rows=[
                B3CapitalEventEvidenceResponse(
                    status=row.status,
                    approval_date=row.approval_date,
                    kind=row.kind,
                    factor=row.factor,
                    last_date_prior=row.last_date_prior,
                    isin_code=row.isin_code,
                    asset_issued=row.asset_issued,
                    remarks=row.remarks,
                )
                for row in reconciliation.rows
            ],
        )

    return ShareCountProvenanceResponse(
        requested_year=provenance.requested_year,
        filed_year=provenance.filed_year,
        status=provenance.status,
        source=provenance.source,
        issued=_share_counts_response(provenance.issued),
        outstanding=_share_counts_response(provenance.outstanding),
        treasury=_capital_composition_response(provenance.treasury),
        restatement_factor=provenance.restatement_factor,
        actions=[action_response(action) for action in provenance.actions],
        evidence=list(provenance.evidence),
        b3_reconciliation=b3_response(provenance.b3_reconciliation),
    )


def _cpc41_window_response(
    provenance: Cpc41WindowProvenance | None,
) -> Cpc41WindowProvenanceResponse | None:
    if provenance is None:
        return None

    def period_response(
        period: Cpc41PeriodProvenance,
    ) -> Cpc41PeriodProvenanceResponse:
        return Cpc41PeriodProvenanceResponse(
            reference_date=period.reference_date,
            disclosure_status=period.disclosure_status,
            class_status=period.class_status,
            multiplier_status=period.multiplier_status,
            multiplier=period.multiplier,
            basic_weighted_shares=period.basic_weighted_shares,
            basic_weighted_shares_status=period.basic_weighted_shares_status,
            diluted_weighted_shares=period.diluted_weighted_shares,
            diluted_weighted_shares_status=period.diluted_weighted_shares_status,
            basic_blocker=period.basic_blocker,
            diluted_blocker=period.diluted_blocker,
            basic_disclosure_status=period.basic_disclosure_status,
            diluted_disclosure_status=period.diluted_disclosure_status,
            basic_class_status=period.basic_class_status,
            diluted_class_status=period.diluted_class_status,
            basic_multiplier_status=period.basic_multiplier_status,
            diluted_multiplier_status=period.diluted_multiplier_status,
            source_accounts=[
                Cpc41AccountEvidenceResponse(
                    module=account.module,
                    code=account.code,
                    name=account.name,
                    selection_status=account.selection_status,
                    value=account.value,
                    basis=account.basis,
                    expected=account.expected,
                )
                for account in period.source_accounts
            ],
        )

    return Cpc41WindowProvenanceResponse(
        selected_periods=[
            period_response(period) for period in provenance.selected_periods
        ],
        basic_blocker=provenance.basic_blocker,
        diluted_blocker=provenance.diluted_blocker,
    )


def _to_response(analysis: TickerAnalysis) -> AnalysisResponse:
    evidence = analysis.debt_evidence

    def line_to_response(line: DebtLineEvidence) -> DebtLineResponse:
        return DebtLineResponse(
            code=line.code,
            name=line.name,
            value=line.value,
            role=line.role,
            reason=line.reason,
            instrument=line.instrument,
            classification=line.classification,
        )

    evidence_response = (
        None
        if evidence is None
        else DebtEvidenceResponse(
            regime=evidence.regime,
            regime_source=evidence.regime_source,
            identity_status=evidence.identity_status,
            used_lines=[line_to_response(line) for line in evidence.used_lines],
            excluded_lines=[line_to_response(line) for line in evidence.excluded_lines],
            included_instruments=list(evidence.included_instruments),
            primary_blocker=evidence.primary_blocker,
            secondary_blockers=list(evidence.secondary_blockers),
        )
    )
    indicator_response = IndicatorsResponse.model_validate(
        analysis.indicators, from_attributes=True
    ).model_copy(
        update={
            "null_reasons": {
                key: reason
                for key, reason in analysis.indicators.null_reasons.items()
                if key in public_indicator_names()
            },
            "source_account_evidence": [
                SourceAccountEvidenceResponse(
                    field=item.field,
                    statement=item.statement,
                    status=item.status,
                    expected=list(item.expected),
                    found=[
                        SourceAccountRefResponse(
                            code=ref.code,
                            name=ref.name,
                            value=ref.value,
                            column=ref.column,
                        )
                        for ref in item.found
                    ],
                    parent_code=item.parent_code,
                    formula=item.formula,
                    dependencies=list(item.dependencies),
                    blocker=item.blocker,
                    consumer_indicators=[
                        key
                        for key in item.consumer_indicators
                        if key in public_indicator_names()
                    ],
                    duplicates_discarded=item.duplicates_discarded,
                )
                for item in analysis.indicators.source_account_evidence
                if not is_retired_sector_input(item.field)
                and item.field != "dividends_declared"
                and not item.field.startswith("dividends_declared[")
            ],
            "cpc41_window_provenance": _cpc41_window_response(
                analysis.indicators.cpc41_window_provenance
            ),
        }
    )
    return AnalysisResponse(
        ticker=analysis.ticker,
        view=analysis.view,
        governance=GovernanceResponse(
            ipo_date=analysis.governance.ipo_date,
            listing_segment=analysis.governance.listing_segment,
            listing_observed_on=analysis.governance.listing_observed_on,
            listing_source=analysis.governance.listing_source,
            tag_along_source=analysis.governance.tag_along_source,
            tag_along_reference=analysis.governance.tag_along_reference,
            blocker=analysis.governance.blocker,
        ),
        classification=ClassificationResponse(
            setor=analysis.classification.setor,
            subsetor=analysis.classification.subsetor,
            segmento=analysis.classification.segmento,
        ),
        reference_date=analysis.reference_date,
        computed_at=analysis.computed_at,
        calculation_contract_version=analysis.calculation_contract_version,
        filed_regime=analysis.filed_regime,
        regime_source=analysis.regime_source,
        issuer=analysis.issuer_name,
        cd_cvm=analysis.cd_cvm,
        cnpj=analysis.cnpj,
        price=analysis.price,
        price_source_code=analysis.price_source_code,
        price_source_session=analysis.price_source_session,
        price_adjusted=analysis.price_adjusted,
        price_basis=analysis.price_basis,
        share_count_basis=analysis.share_count_basis,
        liquidity_basis=analysis.liquidity_basis,
        debt_basis=analysis.debt_basis,
        debt_evidence_snapshot=analysis.debt_evidence_snapshot
        or (DebtEvidenceSnapshot.LEGACY if evidence is None else None),
        debt_evidence=evidence_response,
        roic_tax_basis=analysis.roic_tax_basis,
        share_class_mappings=[
            _mapping_response(mapping) for mapping in analysis.share_class_mappings
        ],
        class_market_values=[
            _class_market_value_response(value)
            for value in analysis.class_market_values
        ],
        capital_provenance=_capital_provenance_response(analysis.capital_provenance),
        income_statement=(
            None
            if analysis.income_statement is None
            else IncomeStatementResponse.model_validate(
                analysis.income_statement, from_attributes=True
            )
        ),
        cash_flow_statement=[
            SourceAccountRefResponse(
                code=item.code,
                name=item.name,
                value=item.value,
                column=item.column,
            )
            for item in analysis.cash_flow_statement
        ],
        indicators=indicator_response,
        indicator_contract=_to_indicator_contract(analysis),
    )


@app.get("/analysis", response_model=list[AnalysisResponse])
async def list_analysis() -> list[AnalysisResponse]:
    """Latest analysis for every ticker that has one."""
    return [_to_response(a) for a in await _repository.all_latest()]


@app.get("/analysis/{ticker}", response_model=TickerViewsResponse)
async def get_analysis(ticker: str) -> TickerViewsResponse:
    """Both views for one ticker: the live TTM plus the closed-year history.

    404 only when the ticker has neither a TTM nor any closed year computed.
    """
    symbol = ticker.upper()
    ttm = await _repository.latest(symbol)
    history = await _repository.history(symbol)
    if ttm is None and not history:
        raise HTTPException(status_code=404, detail=f"No analysis for {ticker}")
    return TickerViewsResponse(
        ticker=symbol,
        ttm=_to_response(ttm) if ttm is not None else None,
        history=[_to_response(a) for a in history],
    )


class CashDividendEventResponse(BaseModel):
    """One B3 cash event, kept on the share base B3 filed at the time."""

    event_type: str | None
    share_class: str
    effective_date: date
    last_with_right: date | None
    approval_date: date | None
    amount_per_share: Decimal | None
    payment_dates: list[date]


class CashDividendHistoryResponse(BaseModel):
    ticker: str
    coverage: Literal["available", "empty", "unavailable", "unresolved"]
    reason: str | None
    events: list[CashDividendEventResponse]


def _b3_cash_share_class(analysis: TickerAnalysis, ticker: str) -> str | None:
    """Resolve the ticker's filed B3 class before reading its cash history."""
    matches = [
        mapping
        for mapping in analysis.share_class_mappings
        if mapping.symbol == ticker
        or any(item.symbol == ticker for item in mapping.code_evidence)
    ]
    if matches:
        if len(matches) != 1:
            return None
        mapping = matches[0]
        if (
            mapping.status is not ShareClassMappingStatus.RESOLVED
            or mapping.economic_rights is not EconomicRightsStatus.RESOLVED
        ):
            return None
        return (
            mapping.per_share_class.value
            if mapping.per_share_class is not None
            else None
        )

    return None


@app.get("/dividends/{ticker}/history", response_model=CashDividendHistoryResponse)
async def get_dividend_history(
    ticker: str, request: Request
) -> CashDividendHistoryResponse:
    """Read one share class's B3 cash events without calculating or persisting."""
    symbol = ticker.strip().upper()
    analysis = await _repository.latest(symbol)
    if analysis is None:
        history = await _repository.history(symbol)
        analysis = history[-1] if history else None
    if analysis is None:
        raise HTTPException(status_code=404, detail=f"No analysis for {ticker}")

    share_class = _b3_cash_share_class(analysis, symbol)
    if share_class is None:
        return CashDividendHistoryResponse(
            ticker=symbol,
            coverage="unresolved",
            reason="unresolved_share_class",
            events=[],
        )

    database = request.app.state.mongo_database
    reader = MongoCashEventReader(
        database["raw_ingestions"],
        registrant_resolver=lambda _ticker: analysis.cd_cvm,
        validation_collection=database["ingestion_validations"],
    )
    events = await reader.cash_events_for_class(symbol, share_class=share_class)
    if events is None:
        return CashDividendHistoryResponse(
            ticker=symbol,
            coverage="unavailable",
            reason="cash_distribution_coverage_unavailable",
            events=[],
        )

    ordered = tuple(sorted(events, key=lambda item: item.effective, reverse=True))
    payment_dates = await reader.payment_dates(symbol, ordered)
    return CashDividendHistoryResponse(
        ticker=symbol,
        coverage="available" if ordered else "empty",
        reason=None,
        events=[
            CashDividendEventResponse(
                event_type=event.event_type,
                share_class=event.share_class or share_class,
                effective_date=event.effective,
                last_with_right=event.last_with_right,
                approval_date=event.approval_date,
                amount_per_share=event.amount_per_share,
                payment_dates=list(payment_dates[index]),
            )
            for index, event in enumerate(ordered)
        ],
    )


class HistoricalCloseResponse(BaseModel):
    """One daily close and the underlying B3 observation."""

    session: date
    code: str
    as_traded: Decimal
    adjusted: Decimal
    factor: Decimal


class PriceHistoryGapResponse(BaseModel):
    year: int
    reason: str


class PriceHistoryResponse(BaseModel):
    ticker: str
    computed_at: datetime
    start_year: int
    end_year: int
    contract_version: str
    price_basis: str
    source: str
    points: list[HistoricalCloseResponse]
    gaps: list[PriceHistoryGapResponse]


def _to_price_history_response(history: PriceHistory) -> PriceHistoryResponse:
    return PriceHistoryResponse(
        ticker=history.ticker,
        computed_at=history.computed_at,
        start_year=history.start_year,
        end_year=history.end_year,
        contract_version=history.contract_version,
        price_basis=history.price_basis,
        source=history.source,
        points=[
            HistoricalCloseResponse(
                session=item.session,
                code=item.code,
                as_traded=item.as_traded,
                adjusted=item.adjusted,
                factor=item.factor,
            )
            for item in history.points
        ],
        gaps=[
            PriceHistoryGapResponse(year=item.year, reason=item.reason)
            for item in history.gaps
        ],
    )


@app.get("/prices/{ticker}/history", response_model=PriceHistoryResponse)
async def get_price_history(ticker: str) -> PriceHistoryResponse:
    """Read daily history prepared by the CLI, without source access or writes."""
    history = await _price_history.execute(ticker)
    if history is None:
        raise HTTPException(status_code=404, detail="price_history_not_prepared")
    return _to_price_history_response(history)


@app.get("/portfolio", response_model=list[PortfolioTickerResponse])
async def list_portfolio() -> list[PortfolioTickerResponse]:
    """Every favorited ticker, oldest favorite first."""
    return [_to_portfolio_response(p) for p in await _portfolio.list()]


@app.post("/portfolio/{ticker}", response_model=PortfolioTickerResponse)
async def add_to_portfolio(ticker: str) -> PortfolioTickerResponse:
    """Favorite a ticker. Idempotent — favoriting one already in stays a no-op.

    422 only on a ticker that does not even have the *shape* of a B3 trading
    code (``is_trading_code``) — not a registry lookup: the front-end only ever
    shows the favorite button on a ticker page that already loaded real
    analysis data, so a shaped ticker reaching here is already established.
    """
    try:
        entry = await _portfolio.add(ticker)
    except UnknownTickerError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return _to_portfolio_response(entry)


@app.delete("/portfolio/{ticker}", status_code=204)
async def remove_from_portfolio(ticker: str) -> None:
    """Un-favorite a ticker. Idempotent — removing one already absent is a no-op."""
    await _portfolio.remove(ticker)
