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

from smaug.analysis.domain.entities import TickerAnalysis
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


class ClassificationResponse(BaseModel):
    """The B3 economic taxonomy: setor → subsetor → segmento (ADR 0024)."""

    setor: str
    subsetor: str | None
    segmento: str | None


class AnalysisResponse(BaseModel):
    """One ticker's analysis for a single view: provenance + indicators."""

    ticker: str
    view: str
    classification: ClassificationResponse
    reference_date: date
    computed_at: datetime
    price: Decimal | None
    price_adjusted: Decimal | None
    price_basis: str | None
    share_count_basis: str | None
    liquidity_basis: str | None
    debt_basis: str | None
    roic_tax_basis: str | None
    indicators: IndicatorsResponse


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


def _to_response(analysis: TickerAnalysis) -> AnalysisResponse:
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
        price=analysis.price,
        price_adjusted=analysis.price_adjusted,
        price_basis=analysis.price_basis,
        share_count_basis=analysis.share_count_basis,
        liquidity_basis=analysis.liquidity_basis,
        debt_basis=analysis.debt_basis,
        roic_tax_basis=analysis.roic_tax_basis,
        indicators=IndicatorsResponse.model_validate(
            analysis.indicators, from_attributes=True
        ),
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
