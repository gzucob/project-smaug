"""FastAPI read API for the computed indicators (Phase 2 delivery).

Serves the latest persisted analysis per ticker as JSON — the surface the
front-end will consume. This is the composition root for the read side: it wires
the Postgres repository and maps domain entities to Pydantic response models.
Computation/persistence is the ``analyze`` CLI command; this only reads.
"""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

from smaug.analysis.domain.entities import TickerAnalysis
from smaug.analysis.infrastructure.sql_repository import SqlAlchemyAnalysisRepository
from smaug.shared.config import get_settings
from smaug.shared.sql_db import create_engine, create_session_factory

_settings = get_settings()
_repository = SqlAlchemyAnalysisRepository(
    create_session_factory(create_engine(_settings))
)

app = FastAPI(title="smaug — análise fundamentalista", version="0.1.0")


class IndicatorsResponse(BaseModel):
    """The computed indicators.

    ``null_reasons`` names why each null field is null (#30's vocabulary:
    inapplicable_regime, source_account_unmapped, source_account_absent,
    missing_price, missing_share_count, missing_prior_period,
    unexpected_regime), keyed by the field's name. A null field with no entry
    is unclassified.
    """

    roe: Decimal | None
    roa: Decimal | None
    roic: Decimal | None
    net_margin: Decimal | None
    gross_margin: Decimal | None
    ebit_margin: Decimal | None
    ebitda_margin: Decimal | None
    asset_turnover: Decimal | None
    eps: Decimal | None
    bvps: Decimal | None
    net_debt: Decimal | None
    net_debt_to_ebitda: Decimal | None
    debt_to_equity: Decimal | None
    liabilities_to_assets: Decimal | None
    current_ratio: Decimal | None
    revenue_growth: Decimal | None
    net_income_growth: Decimal | None
    pe: Decimal | None
    pb: Decimal | None
    psr: Decimal | None
    price_to_assets: Decimal | None
    price_to_ebit: Decimal | None
    price_to_working_capital: Decimal | None
    payout: Decimal | None
    dividend_yield: Decimal | None
    ev_ebitda: Decimal | None
    fcf: Decimal | None
    price_to_fcf: Decimal | None
    fcf_yield: Decimal | None
    net_interest_margin: Decimal | None
    efficiency_ratio: Decimal | None
    cost_of_risk: Decimal | None
    revenue: Decimal | None
    net_income: Decimal | None
    dividends: Decimal | None
    market_cap: Decimal | None
    enterprise_value: Decimal | None
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
    indicators: IndicatorsResponse


class TickerViewsResponse(BaseModel):
    """Both perspectives for one ticker: the live TTM plus the closed-year history."""

    ticker: str
    ttm: AnalysisResponse | None
    history: list[AnalysisResponse]  # closed years, oldest → newest


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
