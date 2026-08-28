"""Immutable outcomes for one analysis run and its ticker-level results."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum

from smaug.analysis.domain.entities import TickerAnalysis


class AnalysisStatus(StrEnum):
    """How one ticker fared in an analysis run."""

    ANALYZED = "analyzed"
    SKIPPED = "skipped"  # a named source/period outcome — not an error
    ERROR = "error"


class NoAnalysisReason(StrEnum):
    """Why mirrored inputs produced no persisted analysis view."""

    NO_MIRRORED_FUNDAMENTALS = "no_mirrored_fundamentals"
    NO_FOUR_QUARTER_WINDOW = "no_four_quarter_window"
    ALL_EXERCISES_PRE_FIRST_B3_SESSION = "all_exercises_pre_first_b3_session"
    UNRESOLVED_SECURITY_IDENTITY = "unresolved_security_identity"


@dataclass(frozen=True, slots=True)
class AnalysisOutcome:
    """Durable status for one ticker in one analysis run."""

    run_id: str
    ticker: str
    status: AnalysisStatus
    recorded_at: datetime
    no_analysis_reason: NoAnalysisReason | None = None
    detail: str = ""

    @property
    def timestamp(self) -> datetime:
        """Compatibility alias for consumers that call the field a timestamp."""
        return self.recorded_at


@dataclass(frozen=True, slots=True)
class TickerOutcome:
    """One ticker's result: its views, or why there are none."""

    ticker: str
    status: AnalysisStatus
    analyses: tuple[TickerAnalysis, ...]
    detail: str = ""
    no_analysis_reason: NoAnalysisReason | None = None
    # These are populated by ``AnalyzePortfolioUseCase.execute``. They remain
    # optional so existing fixtures that construct a ticker result directly
    # keep their original shape.
    run_id: str | None = None
    recorded_at: datetime | None = None


@dataclass(frozen=True, slots=True)
class AnalysisRun:
    """Everything one analysis run produced, per ticker."""

    outcomes: tuple[TickerOutcome, ...]
    run_id: str | None = None
    recorded_at: datetime | None = None

    @property
    def analyses(self) -> list[TickerAnalysis]:
        """Every view computed, flattened — what the CLI renders."""
        return [a for outcome in self.outcomes for a in outcome.analyses]

    @property
    def failed(self) -> tuple[TickerOutcome, ...]:
        return tuple(o for o in self.outcomes if o.status is AnalysisStatus.ERROR)
