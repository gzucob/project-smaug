"""PostgreSQL implementation of ``AnalysisRepository`` (async SQLAlchemy)."""

from __future__ import annotations

from datetime import date
from typing import cast

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from smaug.analysis.domain.entities import (
    VIEW_CLOSED_YEAR,
    VIEW_TTM,
    AnalysisView,
    TickerAnalysis,
)
from smaug.analysis.domain.indicators import Indicators, NullReason
from smaug.analysis.infrastructure.sqlalchemy_models import TickerAnalysisRow
from smaug.portfolio.domain.taxonomy import Classification


def _to_row(analysis: TickerAnalysis) -> TickerAnalysisRow:
    i = analysis.indicators
    return TickerAnalysisRow(
        ticker=analysis.ticker,
        view=analysis.view,
        setor=analysis.classification.setor,
        subsetor=analysis.classification.subsetor,
        segmento=analysis.classification.segmento,
        reference_date=analysis.reference_date,
        computed_at=analysis.computed_at,
        price=analysis.price,
        price_adjusted=analysis.price_adjusted,
        price_basis=analysis.price_basis,
        roe=i.roe,
        roe_total=i.roe_total,
        roa=i.roa,
        roa_total=i.roa_total,
        roic=i.roic,
        net_margin=i.net_margin,
        net_margin_total=i.net_margin_total,
        gross_margin=i.gross_margin,
        ebit_margin=i.ebit_margin,
        ebitda_margin=i.ebitda_margin,
        asset_turnover=i.asset_turnover,
        eps=i.eps,
        bvps=i.bvps,
        net_debt=i.net_debt,
        net_debt_to_ebitda=i.net_debt_to_ebitda,
        net_debt_to_ebit=i.net_debt_to_ebit,
        net_debt_to_equity=i.net_debt_to_equity,
        debt_to_equity=i.debt_to_equity,
        liabilities_to_assets=i.liabilities_to_assets,
        equity_to_assets=i.equity_to_assets,
        current_ratio=i.current_ratio,
        revenue_growth=i.revenue_growth,
        net_income_growth=i.net_income_growth,
        pe=i.pe,
        pb=i.pb,
        psr=i.psr,
        price_to_assets=i.price_to_assets,
        price_to_ebit=i.price_to_ebit,
        price_to_working_capital=i.price_to_working_capital,
        payout=i.payout,
        dividend_yield=i.dividend_yield,
        ev_ebitda=i.ev_ebitda,
        ev_ebit=i.ev_ebit,
        fcf=i.fcf,
        price_to_fcf=i.price_to_fcf,
        fcf_yield=i.fcf_yield,
        net_interest_margin=i.net_interest_margin,
        efficiency_ratio=i.efficiency_ratio,
        cost_of_risk=i.cost_of_risk,
        revenue=i.revenue,
        net_income=i.net_income,
        net_income_total=i.net_income_total,
        dividends=i.dividends,
        market_cap=i.market_cap,
        enterprise_value=i.enterprise_value,
        shares=i.shares,
        null_reasons={k: v.value for k, v in i.null_reasons.items()},
    )


def _to_entity(row: TickerAnalysisRow) -> TickerAnalysis:
    return TickerAnalysis(
        ticker=row.ticker,
        classification=Classification(row.setor, row.subsetor, row.segmento),
        reference_date=row.reference_date,
        computed_at=row.computed_at,
        price=row.price,
        price_adjusted=row.price_adjusted,
        price_basis=row.price_basis,
        view=cast(AnalysisView, row.view),
        indicators=Indicators(
            roe=row.roe,
            roe_total=row.roe_total,
            roa=row.roa,
            roa_total=row.roa_total,
            roic=row.roic,
            net_margin=row.net_margin,
            net_margin_total=row.net_margin_total,
            gross_margin=row.gross_margin,
            ebit_margin=row.ebit_margin,
            ebitda_margin=row.ebitda_margin,
            asset_turnover=row.asset_turnover,
            eps=row.eps,
            bvps=row.bvps,
            net_debt=row.net_debt,
            net_debt_to_ebitda=row.net_debt_to_ebitda,
            net_debt_to_ebit=row.net_debt_to_ebit,
            net_debt_to_equity=row.net_debt_to_equity,
            debt_to_equity=row.debt_to_equity,
            liabilities_to_assets=row.liabilities_to_assets,
            equity_to_assets=row.equity_to_assets,
            current_ratio=row.current_ratio,
            revenue_growth=row.revenue_growth,
            net_income_growth=row.net_income_growth,
            pe=row.pe,
            pb=row.pb,
            psr=row.psr,
            price_to_assets=row.price_to_assets,
            price_to_ebit=row.price_to_ebit,
            price_to_working_capital=row.price_to_working_capital,
            payout=row.payout,
            dividend_yield=row.dividend_yield,
            ev_ebitda=row.ev_ebitda,
            ev_ebit=row.ev_ebit,
            fcf=row.fcf,
            price_to_fcf=row.price_to_fcf,
            fcf_yield=row.fcf_yield,
            net_interest_margin=row.net_interest_margin,
            efficiency_ratio=row.efficiency_ratio,
            cost_of_risk=row.cost_of_risk,
            revenue=row.revenue,
            net_income=row.net_income,
            net_income_total=row.net_income_total,
            dividends=row.dividends,
            market_cap=row.market_cap,
            enterprise_value=row.enterprise_value,
            shares=row.shares,
            # Pre-vocabulary rows carry NULL: degrade to "unclassified" ({}).
            null_reasons={
                k: NullReason(v) for k, v in (row.null_reasons or {}).items()
            },
        ),
    )


class SqlAlchemyAnalysisRepository:
    """Persists analyses and reads back the latest per ticker."""

    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._session_factory = session_factory

    async def save(self, analysis: TickerAnalysis) -> None:
        async with self._session_factory() as session:
            session.add(_to_row(analysis))
            await session.commit()

    async def latest(self, ticker: str) -> TickerAnalysis | None:
        stmt = (
            select(TickerAnalysisRow)
            .where(
                TickerAnalysisRow.ticker == ticker,
                TickerAnalysisRow.view == VIEW_TTM,
            )
            .order_by(TickerAnalysisRow.computed_at.desc())
            .limit(1)
        )
        async with self._session_factory() as session:
            row = (await session.execute(stmt)).scalars().first()
        return _to_entity(row) if row is not None else None

    async def all_latest(self) -> list[TickerAnalysis]:
        stmt = (
            select(TickerAnalysisRow)
            .where(TickerAnalysisRow.view == VIEW_TTM)
            .order_by(TickerAnalysisRow.computed_at.desc())
        )
        async with self._session_factory() as session:
            rows = (await session.execute(stmt)).scalars().all()
        seen: set[str] = set()
        latest: list[TickerAnalysis] = []
        for row in rows:
            if row.ticker not in seen:
                seen.add(row.ticker)
                latest.append(_to_entity(row))
        return latest

    async def history(self, ticker: str) -> list[TickerAnalysis]:
        """Latest computation per closed fiscal year, oldest → newest."""
        stmt = (
            select(TickerAnalysisRow)
            .where(
                TickerAnalysisRow.ticker == ticker,
                TickerAnalysisRow.view == VIEW_CLOSED_YEAR,
            )
            .order_by(TickerAnalysisRow.computed_at.desc())
        )
        async with self._session_factory() as session:
            rows = (await session.execute(stmt)).scalars().all()
        seen: set[date] = set()
        by_year: list[TickerAnalysis] = []
        for row in rows:  # newest computation first → keep the first per year
            if row.reference_date not in seen:
                seen.add(row.reference_date)
                by_year.append(_to_entity(row))
        return sorted(by_year, key=lambda a: a.reference_date)
