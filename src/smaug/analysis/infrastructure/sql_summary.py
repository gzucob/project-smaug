"""Lean PostgreSQL projection for rankings and portfolio cards."""

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from smaug.analysis.domain.entities import VIEW_TTM
from smaug.analysis.domain.summary import AnalysisSummary
from smaug.analysis.infrastructure.sqlalchemy_models import TickerAnalysisRow as Row
from smaug.portfolio.domain.taxonomy import Classification


class SqlAnalysisSummaryReader:
    """Read only the columns these screens display, preserving stored nulls."""

    def __init__(self, sessions: async_sessionmaker[AsyncSession]) -> None:
        self._sessions = sessions

    async def all_latest(self) -> list[AnalysisSummary]:
        statement = (
            select(
                Row.ticker,
                Row.reference_date,
                Row.setor,
                Row.subsetor,
                Row.segmento,
                Row.issuer_name,
                Row.governance["listing_segment"].as_string().label("listing_segment"),
                Row.price,
                Row.price_as_traded,
                Row.market_cap,
                Row.roe,
                Row.dividend_yield,
                Row.pe_basic,
            )
            .where(Row.view == VIEW_TTM)
            .order_by(Row.computed_at.desc(), Row.id.desc())
        )
        async with self._sessions() as session:
            rows = (await session.execute(statement)).all()
        return [
            AnalysisSummary(
                ticker=row.ticker,
                reference_date=row.reference_date,
                classification=Classification(row.setor, row.subsetor, row.segmento),
                issuer=row.issuer_name,
                listing_segment=row.listing_segment,
                price=row.price,
                price_as_traded=row.price_as_traded,
                market_cap=row.market_cap,
                roe=row.roe,
                dividend_yield=row.dividend_yield,
                pe_basic=row.pe_basic,
            )
            for row in rows
        ]
