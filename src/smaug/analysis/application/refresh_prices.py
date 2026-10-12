"""Recover offline price sessions and refresh their dependent analyses."""

import logging
from collections.abc import Awaitable, Callable, Iterable
from dataclasses import replace
from datetime import UTC, datetime, timedelta

from smaug.analysis.application.analyze import AnalyzePortfolioUseCase
from smaug.analysis.application.price_history import BuildPriceHistoryUseCase
from smaug.analysis.domain.indicators import CALCULATION_CONTRACT_VERSION
from smaug.analysis.domain.ports import AnalysisRepository, PriceProvider
from smaug.analysis.domain.price_history import PriceHistoryRepository
from smaug.analysis.domain.price_refresh import (
    PriceRefreshIssue,
    PriceRefreshStatusRepository,
    RefreshIssueReason,
)
from smaug.shared.errors import SourceError

logger = logging.getLogger(__name__)


class PriceRefreshError(ValueError):
    def __init__(self, reason: RefreshIssueReason) -> None:
        self.reason = reason
        super().__init__(reason)


class RefreshPricesUseCase:
    """Compare complete B3 snapshots; preserve saved data on a failed refresh."""

    def __init__(
        self,
        builder: BuildPriceHistoryUseCase,
        histories: PriceHistoryRepository,
        analyses: AnalysisRepository,
        prices: PriceProvider,
        analyzer: AnalyzePortfolioUseCase,
        status_repository: PriceRefreshStatusRepository | None = None,
    ) -> None:
        self._builder = builder
        self._histories = histories
        self._analyses = analyses
        self._prices = prices
        self._analyzer = analyzer
        self._status_repository = status_repository

    async def execute(self, tickers: Iterable[str]) -> int:
        issues: list[PriceRefreshIssue] = []
        updated = 0
        unchanged = 0
        for ticker in tickers:
            try:
                previous = await self._histories.latest(ticker)
                analysis = await self._analyses.latest(ticker)
                quote = await self._prices.get(ticker)
                if quote.price is None or quote.price_source_session is None:
                    raise PriceRefreshError("quote_unavailable")
                history = await self._builder.execute(
                    ticker,
                    start_year=previous.start_year if previous else 2010,
                    persist=False,
                )
                if previous and not {p.session for p in previous.points}.issubset(
                    p.session for p in history.points
                ):
                    raise PriceRefreshError("history_incomplete")
                changed = (
                    analysis is None
                    or analysis.calculation_contract_version
                    != CALCULATION_CONTRACT_VERSION
                    or analysis.computed_at.date() < datetime.now(UTC).date()
                    or analysis.price != quote.price
                    or analysis.price_as_traded != quote.price_as_traded
                    or analysis.price_source_session != quote.price_source_session
                    or analysis.price_source_code != quote.price_source_code
                    or previous is None
                    or previous.points != history.points
                    or previous.gaps != history.gaps
                )
                if not changed and analysis is not None:
                    for component in analysis.class_market_values:
                        component_quote = await self._prices.get(component.symbol)
                        if component.price != component_quote.price:
                            changed = True
                            break
                if changed:
                    run = await self._analyzer.execute((ticker,))
                    if run.failed or not run.analyses:
                        raise PriceRefreshError("analysis_failed")
                    await self._histories.save(history)
                    updated += 1
                else:
                    unchanged += 1
            except Exception as exc:
                reason: RefreshIssueReason = (
                    exc.reason
                    if isinstance(exc, PriceRefreshError)
                    else "source_error"
                    if isinstance(exc, SourceError)
                    else "unexpected_error"
                )
                issues.append(PriceRefreshIssue(ticker, reason))
                logger.exception("Automatic price refresh failed for %s", ticker)
            if self._status_repository is not None:
                await self._status_repository.save(
                    replace(
                        await self._status_repository.read(),
                        updated_count=updated,
                        unchanged_count=unchanged,
                        issues=tuple(issues),
                    )
                )
        return 1 if issues else 0


class CheckPricesUseCase:
    """Throttle checks across restarts and retain the last successful completion."""

    def __init__(
        self,
        repository: PriceRefreshStatusRepository,
        refresh: Callable[[], Awaitable[int]],
        interval: timedelta,
    ) -> None:
        self._repository = repository
        self._refresh = refresh
        self._interval = interval

    async def execute(self, *, force: bool = False) -> int:
        status = await self._repository.read()
        now = datetime.now(UTC)
        if (
            not force
            and status.state in {"completed", "failed"}
            and status.checked_at is not None
            and (status.started_at is None or status.checked_at >= status.started_at)
            and now - status.checked_at < self._interval
        ):
            return 0
        await self._repository.save(
            replace(
                status,
                state="running",
                started_at=now,
                updated_count=None,
                unchanged_count=None,
                issues=(),
            )
        )
        try:
            result = await self._refresh()
        except Exception:
            logger.exception("Automatic price check failed")
            result = 1
        finished = datetime.now(UTC)
        await self._repository.save(
            replace(
                await self._repository.read(),
                state="failed" if result else "completed",
                started_at=now,
                checked_at=finished,
                completed_at=status.completed_at if result else finished,
            )
        )
        return result
