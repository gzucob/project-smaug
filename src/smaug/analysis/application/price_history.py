"""Prepare daily price history in the CLI and read persisted snapshots."""

from datetime import UTC, date, datetime

from smaug.analysis.domain.capital import factor_at
from smaug.analysis.domain.price_history import (
    DailyHistorySource,
    HistoricalClose,
    PriceHistory,
    PriceHistoryGap,
    PriceHistoryRepository,
    RestatementReader,
)


class BuildPriceHistoryUseCase:
    """Restate each session individually and append an atomic snapshot."""

    def __init__(
        self,
        source: DailyHistorySource,
        shares: RestatementReader,
        repository: PriceHistoryRepository,
    ) -> None:
        self._source = source
        self._shares = shares
        self._repository = repository

    async def execute(
        self, ticker: str, start_year: int = 2010, end_year: int | None = None
    ) -> PriceHistory:
        end = end_year if end_year is not None else date.today().year
        # CVM inputs begin in 2010. Older tape includes earlier currencies and
        # capital moves outside our adjustment evidence, so it cannot be
        # published as a BRL series on the snapshot's current share base.
        if not 2010 <= start_year <= end <= date.today().year:
            raise ValueError("history years must fall between 2010 and today")
        symbol = ticker.strip().upper()
        computed_at = datetime.now(UTC)
        timeline = await self._shares.restatement_timeline(symbol)
        points: list[HistoricalClose] = []
        gaps: list[PriceHistoryGap] = []
        for year in range(start_year, end + 1):
            sessions, reason = await self._source.history_year(symbol, year)
            if reason is not None or not sessions:
                gaps.append(PriceHistoryGap(year, reason or "no_trading_sessions"))
                continue
            for observation in sorted(sessions, key=lambda item: item.session):
                factor = factor_at(timeline, observation.session)
                if factor <= 0:
                    raise ValueError("restatement factors must be positive")
                points.append(
                    HistoricalClose(
                        session=observation.session,
                        code=observation.code,
                        as_traded=observation.close,
                        adjusted=observation.close / factor,
                        factor=factor,
                    )
                )
        history = PriceHistory(
            ticker=symbol,
            computed_at=computed_at,
            start_year=start_year,
            end_year=end,
            points=tuple(points),
            gaps=tuple(gaps),
        )
        await self._repository.save(history)
        return history


class ReadPriceHistoryUseCase:
    """Read a prepared history without invoking market or filing readers."""

    def __init__(self, repository: PriceHistoryRepository) -> None:
        self._repository = repository

    async def execute(self, ticker: str) -> PriceHistory | None:
        return await self._repository.latest(ticker.strip().upper())
