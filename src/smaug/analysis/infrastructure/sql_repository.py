"""PostgreSQL implementation of ``AnalysisRepository`` (async SQLAlchemy)."""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from smaug.analysis.domain.entities import TickerAnalysis
from smaug.analysis.domain.indicators import Indicators
from smaug.analysis.infrastructure.sqlalchemy_models import TickerAnalysisRow
from smaug.portfolio.domain.sectors import Sector


def _to_row(analysis: TickerAnalysis) -> TickerAnalysisRow:
    i = analysis.indicators
    return TickerAnalysisRow(
        ticker=analysis.ticker,
        sector=analysis.sector.value,
        reference_date=analysis.reference_date,
        computed_at=analysis.computed_at,
        price=analysis.price,
        price_nominal=analysis.price_nominal,
        price_basis=analysis.price_basis,
        roe=i.roe,
        roa=i.roa,
        net_margin=i.net_margin,
        gross_margin=i.gross_margin,
        ebitda_margin=i.ebitda_margin,
        net_debt=i.net_debt,
        net_debt_to_ebitda=i.net_debt_to_ebitda,
        current_ratio=i.current_ratio,
        revenue_growth=i.revenue_growth,
        net_income_growth=i.net_income_growth,
        pe=i.pe,
        pb=i.pb,
        dividend_yield=i.dividend_yield,
        ev_ebitda=i.ev_ebitda,
    )


def _to_entity(row: TickerAnalysisRow) -> TickerAnalysis:
    return TickerAnalysis(
        ticker=row.ticker,
        sector=Sector(row.sector),
        reference_date=row.reference_date,
        computed_at=row.computed_at,
        price=row.price,
        price_nominal=row.price_nominal,
        price_basis=row.price_basis,
        indicators=Indicators(
            roe=row.roe,
            roa=row.roa,
            net_margin=row.net_margin,
            gross_margin=row.gross_margin,
            ebitda_margin=row.ebitda_margin,
            net_debt=row.net_debt,
            net_debt_to_ebitda=row.net_debt_to_ebitda,
            current_ratio=row.current_ratio,
            revenue_growth=row.revenue_growth,
            net_income_growth=row.net_income_growth,
            pe=row.pe,
            pb=row.pb,
            dividend_yield=row.dividend_yield,
            ev_ebitda=row.ev_ebitda,
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
            .where(TickerAnalysisRow.ticker == ticker)
            .order_by(TickerAnalysisRow.computed_at.desc())
            .limit(1)
        )
        async with self._session_factory() as session:
            row = (await session.execute(stmt)).scalars().first()
        return _to_entity(row) if row is not None else None

    async def all_latest(self) -> list[TickerAnalysis]:
        stmt = select(TickerAnalysisRow).order_by(TickerAnalysisRow.computed_at.desc())
        async with self._session_factory() as session:
            rows = (await session.execute(stmt)).scalars().all()
        seen: set[str] = set()
        latest: list[TickerAnalysis] = []
        for row in rows:
            if row.ticker not in seen:
                seen.add(row.ticker)
                latest.append(_to_entity(row))
        return latest
