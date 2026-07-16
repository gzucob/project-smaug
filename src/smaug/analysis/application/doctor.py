"""Coverage report over the persisted analysis (#47 — the M0 gate).

Answers *"what is true right now"* about the derived data (RULES_DOCS: that
answer must come from a command, never from prose). For every persisted
exercise of every ticker it reports, per indicator, a **known status**: a
value, a null with a named cause (the ``NullReason`` vocabulary of #30/ADR
0008, attributed upstream by the calculator), or an *unclassified* null — a
reportable status of its own, never a silent omission.

Read-only: it reads back through the ``AnalysisRepository`` port and never
recomputes or persists (``CLAUDE.md``: the ``analyze`` CLI is the only write
surface). The classification is not redone here — it reads the reason each null
was born with.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable
from dataclasses import dataclass
from datetime import date

from smaug.analysis.domain.entities import AnalysisView, TickerAnalysis
from smaug.analysis.domain.indicators import (
    Indicators,
    NullReason,
    indicator_names,
)
from smaug.analysis.domain.ports import AnalysisRepository
from smaug.portfolio.domain.sectors import Sector, sector_of

# See ``analyze.SectorResolver``: the curated nine by default, a registry-backed
# resolver for on-demand tickers, injected at the composition root.
SectorResolver = Callable[[str], Sector]


@dataclass(frozen=True)
class IndicatorCoverage:
    """The status of one indicator in one exercise.

    ``has_value`` → a value was computed. Otherwise ``reason`` names the cause,
    or is ``None`` for an *unclassified* null (e.g. a zero denominator).
    """

    indicator: str
    has_value: bool
    reason: NullReason | None

    @property
    def is_unclassified(self) -> bool:
        return not self.has_value and self.reason is None

    @property
    def status(self) -> str:
        """A single enumerable label: ``value``, a ``NullReason``, ``unclassified``."""
        if self.has_value:
            return "value"
        return self.reason.value if self.reason is not None else "unclassified"


@dataclass(frozen=True)
class ExerciseCoverage:
    """Coverage of every indicator for one ticker/view/period."""

    view: AnalysisView
    reference_date: date
    indicators: tuple[IndicatorCoverage, ...]

    @property
    def values(self) -> int:
        return sum(1 for c in self.indicators if c.has_value)

    @property
    def named_nulls(self) -> int:
        return sum(
            1 for c in self.indicators if not c.has_value and c.reason is not None
        )

    @property
    def unclassified(self) -> int:
        return sum(1 for c in self.indicators if c.is_unclassified)


@dataclass(frozen=True)
class TickerCoverage:
    """Every persisted exercise for one ticker (TTM first, then closed years).

    An empty ``exercises`` means nothing is persisted for the ticker — itself a
    reportable state, not a silent gap.
    """

    ticker: str
    sector: Sector
    exercises: tuple[ExerciseCoverage, ...]


@dataclass(frozen=True)
class DoctorReport:
    """The full coverage report: one entry per requested ticker."""

    tickers: tuple[TickerCoverage, ...]


def _coverage_of(indicators: Indicators) -> tuple[IndicatorCoverage, ...]:
    """Classify every indicator cell as value / named-null / unclassified."""
    cells: list[IndicatorCoverage] = []
    for name in indicator_names():
        has_value = getattr(indicators, name) is not None
        reason = None if has_value else indicators.null_reasons.get(name)
        cells.append(IndicatorCoverage(name, has_value=has_value, reason=reason))
    return tuple(cells)


def _exercise_of(analysis: TickerAnalysis) -> ExerciseCoverage:
    return ExerciseCoverage(
        view=analysis.view,
        reference_date=analysis.reference_date,
        indicators=_coverage_of(analysis.indicators),
    )


class DoctorUseCase:
    """Build the coverage report from the persisted analysis (read-only)."""

    def __init__(
        self,
        repository: AnalysisRepository,
        *,
        sector_resolver: SectorResolver = sector_of,
    ) -> None:
        self._repository = repository
        self._sector_resolver = sector_resolver

    async def execute(self, tickers: Iterable[str]) -> DoctorReport:
        coverages: list[TickerCoverage] = []
        for ticker in tickers:
            exercises: list[ExerciseCoverage] = []
            ttm = await self._repository.latest(ticker)
            if ttm is not None:
                exercises.append(_exercise_of(ttm))
            for closed in await self._repository.history(ticker):
                exercises.append(_exercise_of(closed))
            coverages.append(
                TickerCoverage(ticker, self._sector_resolver(ticker), tuple(exercises))
            )
        return DoctorReport(tuple(coverages))
