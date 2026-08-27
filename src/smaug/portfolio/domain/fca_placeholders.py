"""Findings produced while recovering unusable FCA trading-code rows.

The FCA securities member is a regulator-owned identity source, but its
``Codigo_Negociacao`` column also contains blanks, registration numbers and
other free text.  These values are kept as an audit population rather than
silently discarded.  A recovery finding records the evidence chain used (or
the reason it stopped), so the current identity snapshot remains reproducible.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from enum import StrEnum

from smaug.portfolio.domain.company import CompanyIdentity, InstrumentKind
from smaug.portfolio.domain.share_classes import (
    PerShareClass,
    UnitComponent,
)


class FcaCodeIssue(StrEnum):
    """Why an FCA ``Codigo_Negociacao`` cannot be used as a B3 code."""

    BLANK = "blank"
    NUMERIC_PLACEHOLDER = "numeric_placeholder"
    MALFORMED = "malformed"


class FcaRecoveryStatus(StrEnum):
    """Outcome of the official B3 recovery chain for one FCA row."""

    RECOVERED = "recovered"
    UNRESOLVED = "unresolved"


@dataclass(frozen=True, slots=True)
class FcaPlaceholderRow:
    """One affected FCA securities row, including its original line number."""

    row_number: int
    cnpj: str
    cd_cvm: str | None
    denom: str
    raw_code: str
    code_issue: FcaCodeIssue
    instrument_kind: InstrumentKind
    instrument_type: str
    cvm_sector: str = ""
    situation: str = ""
    listed_since: date | None = None
    trading_ended: date | None = None
    per_share_class: PerShareClass | None = None
    unit_components: tuple[UnitComponent, ...] = ()
    shares_per_unit: int | None = None

    @property
    def is_current(self) -> bool:
        """Whether the FCA row has no recorded end of its trading segment."""
        return self.trading_ended is None


@dataclass(frozen=True, slots=True)
class FcaPlaceholderFinding:
    """Auditable recovery result for one affected FCA row."""

    row: FcaPlaceholderRow
    status: FcaRecoveryStatus
    reason: str
    candidate_codes: tuple[str, ...] = ()
    observed_codes: tuple[str, ...] = ()
    recovered_codes: tuple[str, ...] = ()
    official_root: str | None = None
    window_start: date | None = None
    window_end: date | None = None
    evidence: tuple[str, ...] = ()
    detail: str | None = None

    @property
    def recovered(self) -> bool:
        """Convenience predicate used by report renderers."""
        return self.status is FcaRecoveryStatus.RECOVERED

    def unresolved(
        self, reason: str, *, detail: str | None = None
    ) -> FcaPlaceholderFinding:
        """Return this finding with a code rejected after a collision check."""
        return FcaPlaceholderFinding(
            row=self.row,
            status=FcaRecoveryStatus.UNRESOLVED,
            reason=reason,
            candidate_codes=self.candidate_codes,
            observed_codes=self.observed_codes,
            official_root=self.official_root,
            window_start=self.window_start,
            window_end=self.window_end,
            evidence=self.evidence,
            detail=detail,
        )


@dataclass(frozen=True, slots=True)
class FcaPlaceholderReport:
    """Deterministic inventory and recovery report for one FCA snapshot."""

    snapshot_year: int
    findings: tuple[FcaPlaceholderFinding, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "findings",
            tuple(sorted(self.findings, key=_finding_key)),
        )

    @property
    def rows(self) -> tuple[FcaPlaceholderRow, ...]:
        """All affected FCA rows, in archive order."""
        return tuple(finding.row for finding in self.findings)

    @property
    def recovered(self) -> tuple[FcaPlaceholderFinding, ...]:
        """Rows with at least one officially observed B3 code."""
        return tuple(
            finding
            for finding in self.findings
            if finding.status is FcaRecoveryStatus.RECOVERED
        )

    @property
    def unresolved(self) -> tuple[FcaPlaceholderFinding, ...]:
        """Rows for which the stable official chain did not establish a code."""
        return tuple(
            finding
            for finding in self.findings
            if finding.status is FcaRecoveryStatus.UNRESOLVED
        )

    @property
    def total(self) -> int:
        return len(self.findings)

    @property
    def recovered_count(self) -> int:
        return len(self.recovered)

    @property
    def unresolved_count(self) -> int:
        return len(self.unresolved)

    def finding_for(self, row_number: int) -> FcaPlaceholderFinding | None:
        """Find a row by its source line number."""
        return next(
            (
                finding
                for finding in self.findings
                if finding.row.row_number == row_number
            ),
            None,
        )


@dataclass(frozen=True, slots=True)
class FcaRecoveryResult:
    """Identities to merge into the FCA index and their audit report."""

    identities: tuple[CompanyIdentity, ...] = ()
    report: FcaPlaceholderReport = field(
        default_factory=lambda: FcaPlaceholderReport(snapshot_year=0)
    )


def _finding_key(finding: FcaPlaceholderFinding) -> tuple[int, str, str]:
    row = finding.row
    return row.row_number, row.cnpj, row.raw_code
