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

from datetime import date, datetime
from decimal import Decimal

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from smaug.analysis.domain.entities import VIEW_TTM, TickerAnalysis
from smaug.analysis.domain.financials import (
    AccountingRegime,
    DebtBlocker,
    DebtEvidenceSnapshot,
    DebtIdentityStatus,
    DebtInstrument,
    DebtLineClassification,
    DebtLineEvidence,
    DebtLineRole,
    RegimeSource,
    SourceAccountStatus,
)
from smaug.analysis.domain.indicators import (
    INDICATOR_CONTRACT,
    IndicatorTier,
    NullReason,
)
from smaug.analysis.infrastructure.sql_repository import SqlAlchemyAnalysisRepository
from smaug.portfolio.application.manage_portfolio import ManagePortfolioUseCase
from smaug.portfolio.domain.entities import PortfolioTicker
from smaug.portfolio.infrastructure.sql_repository import SqlAlchemyPortfolioRepository
from smaug.shared.config import get_settings
from smaug.shared.errors import UnknownTickerError
from smaug.shared.sql_db import create_engine, create_session_factory

_settings = get_settings()
_session_factory = create_session_factory(create_engine(_settings))
_repository = SqlAlchemyAnalysisRepository(_session_factory)
_portfolio = ManagePortfolioUseCase(SqlAlchemyPortfolioRepository(_session_factory))

app = FastAPI(title="smaug — análise fundamentalista", version="0.1.0")
# The only cross-origin caller is PR 2's Next.js Route Handler proxying the
# favorite-ticker toggle — every read stays server-side (RULES_FRONTEND), so
# this never needs to admit a browser origin, only that one server.
app.add_middleware(
    CORSMiddleware,
    allow_origins=list(_settings.api_cors_origins),
    allow_methods=["GET", "POST", "DELETE"],
    allow_headers=["*"],
)


class BankRegulatoryProvenanceResponse(BaseModel):
    """Source contract behind bank-specific regulatory ratios."""

    source: str | None
    period_start: date | None
    period_end: date | None
    perimeter: str | None
    averaging_method: str | None
    basis: str | None
    available_inputs: list[str]
    missing_inputs: list[str]
    incompatible_inputs: list[str]


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
    eps: Decimal | None
    eps_basic: Decimal | None
    eps_diluted: Decimal | None
    eps_basic_market: Decimal | None
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
    revenue_growth: Decimal | None
    net_income_growth: Decimal | None
    revenue_cagr_5y: Decimal | None
    ebitda_cagr_5y: Decimal | None
    ebit_cagr_5y: Decimal | None
    net_income_cagr_5y: Decimal | None
    pe_basic: Decimal | None
    pe_diluted: Decimal | None
    pb: Decimal | None
    company_pe: Decimal | None
    company_pb: Decimal | None
    pe_basic_market: Decimal | None
    psr: Decimal | None
    price_to_assets: Decimal | None
    price_to_ebit: Decimal | None
    price_to_working_capital: Decimal | None
    dividend_yield: Decimal | None
    payout_cash_paid_in_period: Decimal | None
    payout_declared_in_period: Decimal | None
    company_cash_yield_paid_in_period: Decimal | None
    company_yield_declared_in_period: Decimal | None
    ev_ebitda: Decimal | None
    ev_ebit: Decimal | None
    fcf: Decimal | None
    price_to_fcf: Decimal | None
    fcf_yield: Decimal | None
    net_interest_margin: Decimal | None
    efficiency_ratio: Decimal | None
    cost_of_risk: Decimal | None
    loss_ratio: Decimal | None
    combined_ratio: Decimal | None
    revenue: Decimal | None
    net_income: Decimal | None
    net_income_total: Decimal | None
    distributions_per_security: Decimal | None
    company_distributions_paid_in_period: Decimal | None
    company_distributions_declared_in_period: Decimal | None
    total_assets: Decimal | None
    total_liabilities: Decimal | None
    equity: Decimal | None
    equity_total: Decimal | None
    market_cap: Decimal | None
    enterprise_value: Decimal | None
    non_controlling_interests: Decimal | None
    shares: Decimal | None
    null_reasons: dict[str, str]
    source_account_evidence: list[SourceAccountEvidenceResponse]
    bank_regulatory_provenance: BankRegulatoryProvenanceResponse | None


class IndicatorContractResponse(BaseModel):
    """Formula and provenance metadata for one market-facing indicator."""

    tier: IndicatorTier
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


class AnalysisResponse(BaseModel):
    """One ticker's analysis for a single view: provenance + indicator contract."""

    ticker: str
    view: str
    classification: ClassificationResponse
    reference_date: date
    computed_at: datetime
    filed_regime: AccountingRegime | None
    regime_source: RegimeSource | None
    issuer: str | None
    cd_cvm: str | None
    cnpj: str | None
    price: Decimal | None
    price_adjusted: Decimal | None
    price_basis: str | None
    share_count_basis: str | None
    liquidity_basis: str | None
    debt_basis: str | None
    debt_evidence_snapshot: DebtEvidenceSnapshot | None
    debt_evidence: DebtEvidenceResponse | None
    roic_tax_basis: str | None
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
            tier=contract.tier,
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
        for key, contract in INDICATOR_CONTRACT.items()
    }


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
                        )
                        for ref in item.found
                    ],
                    parent_code=item.parent_code,
                    formula=item.formula,
                    dependencies=list(item.dependencies),
                    blocker=item.blocker,
                    consumer_indicators=list(item.consumer_indicators),
                )
                for item in analysis.indicators.source_account_evidence
            ],
            "bank_regulatory_provenance": (
                None
                if analysis.indicators.bank_regulatory_provenance is None
                else BankRegulatoryProvenanceResponse(
                    source=analysis.indicators.bank_regulatory_provenance.source,
                    period_start=(
                        analysis.indicators.bank_regulatory_provenance.period_start
                    ),
                    period_end=analysis.indicators.bank_regulatory_provenance.period_end,
                    perimeter=analysis.indicators.bank_regulatory_provenance.perimeter,
                    averaging_method=(
                        analysis.indicators.bank_regulatory_provenance.averaging_method
                    ),
                    basis=analysis.indicators.bank_regulatory_provenance.basis,
                    available_inputs=sorted(
                        analysis.indicators.bank_regulatory_provenance.available_inputs
                    ),
                    missing_inputs=sorted(
                        analysis.indicators.bank_regulatory_provenance.missing_inputs
                    ),
                    incompatible_inputs=sorted(
                        analysis.indicators.bank_regulatory_provenance.incompatible_inputs
                    ),
                )
            ),
        }
    )
    return AnalysisResponse(
        ticker=analysis.ticker,
        view=analysis.view,
        classification=ClassificationResponse(
            setor=analysis.classification.setor,
            subsetor=analysis.classification.subsetor,
            segmento=analysis.classification.segmento,
        ),
        reference_date=analysis.reference_date,
        computed_at=analysis.computed_at,
        filed_regime=analysis.filed_regime,
        regime_source=analysis.regime_source,
        issuer=analysis.issuer_name,
        cd_cvm=analysis.cd_cvm,
        cnpj=analysis.cnpj,
        price=analysis.price,
        price_adjusted=analysis.price_adjusted,
        price_basis=analysis.price_basis,
        share_count_basis=analysis.share_count_basis,
        liquidity_basis=analysis.liquidity_basis,
        debt_basis=analysis.debt_basis,
        debt_evidence_snapshot=analysis.debt_evidence_snapshot
        or (DebtEvidenceSnapshot.LEGACY if evidence is None else None),
        debt_evidence=evidence_response,
        roic_tax_basis=analysis.roic_tax_basis,
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
