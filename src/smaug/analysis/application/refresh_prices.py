"""Recover offline price sessions and refresh their dependent analyses."""

import logging
from collections.abc import Awaitable, Callable, Iterable, Mapping
from contextlib import AbstractAsyncContextManager, nullcontext
from dataclasses import replace
from datetime import UTC, datetime, timedelta

from smaug.analysis.application.analyze import AnalyzePortfolioUseCase
from smaug.analysis.application.price_history import BuildPriceHistoryUseCase
from smaug.analysis.domain.indicators import NullReason
from smaug.analysis.domain.outcomes import AnalysisRun, AnalysisStatus
from smaug.analysis.domain.ports import AnalysisRepository, PriceProvider
from smaug.analysis.domain.price_history import PriceHistory, PriceHistoryRepository
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


class _GroupAnalysisError(Exception):
    def __init__(self, issues: list[PriceRefreshIssue]) -> None:
        self.issues = issues


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

    async def execute(
        self,
        tickers: Iterable[str],
        *,
        issuers: Mapping[str, str] | None = None,
        forced: frozenset[str] = frozenset(),
        blocked: frozenset[str] = frozenset(),
        source_issues: tuple[PriceRefreshIssue, ...] = (),
        deferred: Mapping[str, PriceRefreshIssue] | None = None,
        group_transaction: Callable[
            [], AbstractAsyncContextManager[object]
        ] = nullcontext,
    ) -> int:
        issues = list(source_issues)
        failed = False
        updated = 0
        unchanged = 0
        groups: dict[str, list[str]] = {}
        for ticker in tickers:
            groups.setdefault((issuers or {}).get(ticker, ticker), []).append(ticker)
        for symbols in groups.values():
            waiting = [
                issue
                for symbol in symbols
                if deferred and (issue := deferred.get(symbol))
            ]
            if waiting:
                unchanged += len(symbols)
                issues.extend(_group_issues(waiting, symbols))
                continue
            if not blocked.isdisjoint(symbols):
                unchanged += len(symbols)
                issues.extend(
                    PriceRefreshIssue(symbol, "record_rejected") for symbol in symbols
                )
                continue
            ticker = symbols[0]
            try:
                changed = any(symbol in forced for symbol in symbols)
                histories = []
                for ticker in symbols:
                    history, history_changed, inputs_changed = await self._inspect(
                        ticker
                    )
                    if history_changed:
                        histories.append(history)
                    changed |= inputs_changed
                if changed:
                    async with group_transaction():
                        run = await self._analyzer.execute(symbols)
                        if run.failed or {a.ticker for a in run.analyses} != set(
                            symbols
                        ):
                            raise _GroupAnalysisError(_analysis_issues(run, symbols))
                        for history in histories:
                            ticker = history.ticker
                            await self._histories.save(history)
                    updated += len(symbols)
                else:
                    unchanged += len(symbols)
            except _GroupAnalysisError as error:
                failed = True
                unchanged += len(symbols)
                issues.extend(_group_issues(error.issues, symbols))
                for issue in error.issues:
                    logger.error(
                        "Price refresh analysis for %s: status=%s cause=%s %s",
                        issue.ticker,
                        issue.analysis_status,
                        issue.no_analysis_reason,
                        issue.detail or "",
                    )
            except Exception as exc:
                failed = True
                unchanged += len(symbols)
                reason: RefreshIssueReason = (
                    exc.reason
                    if isinstance(exc, PriceRefreshError)
                    else "source_error"
                    if isinstance(exc, SourceError)
                    else "unexpected_error"
                )
                issues.extend(
                    _group_issues([PriceRefreshIssue(ticker, reason)], symbols)
                )
                logger.exception("Automatic price refresh failed for %s", ticker)
        if self._status_repository is not None:
            status = await self._status_repository.read()
            await self._status_repository.save(
                replace(
                    status,
                    updated_count=updated,
                    unchanged_count=unchanged,
                    issues=tuple(dict.fromkeys((*status.issues, *issues))),
                )
            )
        return int(failed)

    async def _inspect(self, ticker: str) -> tuple[PriceHistory, bool, bool]:
        previous = await self._histories.latest(ticker)
        analysis = await self._analyses.latest(ticker)
        quote = await self._prices.get(ticker)
        known_gap = (
            analysis is not None
            and analysis.price is None
            and quote.price is None
            and quote.price_null_reason
            in {NullReason.MISSING_PRICE, NullReason.PRICE_SYMBOL_NOT_FOUND}
        )
        if not known_gap and (
            quote.price is None or quote.price_source_session is None
        ):
            raise PriceRefreshError("quote_unavailable")
        if (
            analysis is not None
            and analysis.price_source_session is not None
            and (
                quote.price_source_session is None
                or quote.price_source_session < analysis.price_source_session
            )
        ):
            raise PriceRefreshError("history_incomplete")
        history = await self._builder.execute(
            ticker, start_year=previous.start_year if previous else 2010, persist=False
        )
        if previous and not {p.session for p in previous.points}.issubset(
            p.session for p in history.points
        ):
            raise PriceRefreshError("history_incomplete")
        history_changed = (
            previous is None
            or previous.points != history.points
            or previous.gaps != history.gaps
        )
        changed = (
            history_changed
            or analysis is None
            or analysis.price != quote.price
            or analysis.price_as_traded != quote.price_as_traded
            or analysis.price_source_session != quote.price_source_session
            or analysis.price_source_code != quote.price_source_code
        )
        if not changed and analysis is not None:
            for component in analysis.class_market_values:
                if component.price != (await self._prices.get(component.symbol)).price:
                    changed = True
                    break
        return history, history_changed, changed


def _group_issues(
    issues: list[PriceRefreshIssue], symbols: list[str]
) -> list[PriceRefreshIssue]:
    affected = {issue.ticker for issue in issues}
    detail = ", ".join(sorted(affected))
    return issues + [
        PriceRefreshIssue(symbol, "issuer_refresh_deferred", detail=detail)
        for symbol in symbols
        if symbol not in affected
    ]


def _analysis_issues(run: AnalysisRun, symbols: list[str]) -> list[PriceRefreshIssue]:
    produced = {analysis.ticker for analysis in run.analyses}
    outcomes = {outcome.ticker: outcome for outcome in run.outcomes}
    issues = []
    for symbol in symbols:
        outcome = outcomes.get(symbol)
        if symbol in produced and (
            outcome is None or outcome.status != AnalysisStatus.ERROR
        ):
            continue
        if outcome is None:
            issues.append(
                PriceRefreshIssue(
                    symbol, "analysis_failed", detail="No outcome returned"
                )
            )
        else:
            issues.append(
                PriceRefreshIssue(
                    symbol,
                    "analysis_failed",
                    outcome.status,
                    outcome.no_analysis_reason,
                    outcome.detail[:500] or None,
                )
            )
    issues.extend(
        PriceRefreshIssue(
            symbol, "analysis_failed", detail="Unexpected analysis for this group"
        )
        for symbol in sorted(produced.difference(symbols))
    )
    return issues


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
            await self._repository.save(
                replace(await self._repository.read(), updated_count=0)
            )
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
