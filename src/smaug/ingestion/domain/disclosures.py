"""Publication keys and retry policy for structured CVM disclosures."""

from calendar import monthrange
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Literal, Protocol

from smaug.ingestion.domain.ports import RawFetchResult
from smaug.shared.source_inputs import SYNC_RUN_PREFIX as SYNC_RUN_PREFIX

DisclosureKind = Literal["ITR", "DFP", "FRE", "FCA"]
CHECK_INTERVAL = timedelta(days=7)
MAX_PENDING_CHECKS = 8
PUBLISHED_YEARS = 5


@dataclass(frozen=True)
class DisclosureIssuer:
    code: str
    ticker: str
    cnpj: str
    listed_since: date | None = None


@dataclass(frozen=True)
class DisclosureArchive:
    kind: DisclosureKind
    year: int

    @property
    def name(self) -> str:
        return f"{self.kind.lower()}_cia_aberta_{self.year}"


@dataclass(frozen=True)
class DisclosurePeriod:
    code: str
    kind: DisclosureKind
    reference: date


@dataclass(frozen=True)
class ArchiveRevision:
    etag: str | None = None
    last_modified: str | None = None


@dataclass(frozen=True)
class DisclosureEdition:
    revision: ArchiveRevision | None
    versions: dict[DisclosurePeriod, tuple[int, ...]]


@dataclass(frozen=True)
class DisclosureBatch:
    rows: dict[DisclosurePeriod, list[RawFetchResult]]
    issuer_errors: dict[str, str]


class DisclosureSource(Protocol):
    async def inspect(
        self, archive: DisclosureArchive, revision: ArchiveRevision | None
    ) -> DisclosureEdition: ...

    async def read(
        self,
        archive: DisclosureArchive,
        edition: DisclosureEdition,
        targets: dict[DisclosurePeriod, tuple[int, ...]],
    ) -> DisclosureBatch: ...


class IdentityArchiveSource(Protocol):
    async def refresh_identity(
        self, archive: DisclosureArchive, revision: ArchiveRevision | None
    ) -> ArchiveRevision | None: ...


class DisclosureInventory(Protocol):
    async def coverage(self, codes: tuple[str, ...]) -> dict[DisclosurePeriod, int]: ...

    async def claim(self, archive: DisclosureArchive, now: datetime) -> bool: ...

    async def begin_materialization(self, archive: DisclosureArchive) -> None: ...

    async def record_issuer_errors(
        self, archive: DisclosureArchive, checked: set[str], errors: dict[str, str]
    ) -> bool: ...

    async def revision(
        self, archive: DisclosureArchive, scope: str
    ) -> ArchiveRevision | None: ...

    async def complete(
        self, period: DisclosurePeriod, version: int, now: datetime
    ) -> None: ...

    async def pending(self, period: DisclosurePeriod, now: datetime) -> None: ...

    async def unresolved(self, archive: DisclosureArchive) -> set[DisclosurePeriod]: ...

    async def exhausted_versions(
        self, archive: DisclosureArchive
    ) -> set[tuple[DisclosurePeriod, int]]: ...

    async def catalog_only(
        self, period: DisclosurePeriod, version: int, now: datetime
    ) -> None: ...

    async def settle(
        self, archive: DisclosureArchive, scope: str, revision: ArchiveRevision | None
    ) -> None: ...


def shift_months(day: date, offset: int) -> date:
    year, month = divmod(day.year * 12 + day.month - 1 + offset, 12)
    month += 1
    last_day = monthrange(year, month)[1]
    end_of_month = day.day == monthrange(day.year, day.month)[1]
    return date(year, month, last_day if end_of_month else min(day.day, last_day))


def expected_periods(
    archive: DisclosureArchive, issuer: DisclosureIssuer, fiscal_close: date
) -> tuple[DisclosurePeriod, ...]:
    if archive.kind not in ("ITR", "DFP"):
        return ()
    kind = archive.kind
    close = shift_months(fiscal_close, 12 * (archive.year - fiscal_close.year))
    offsets = (0,) if archive.kind == "DFP" else (-9, -6, -3, 3, 6, 9)
    periods = (shift_months(close, offset) for offset in offsets)
    return tuple(
        DisclosurePeriod(issuer.code, kind, day)
        for day in sorted(periods)
        if day.year == archive.year
        and (issuer.listed_since is None or day >= issuer.listed_since)
    )


def expected_by(period: DisclosurePeriod) -> date:
    if period.kind == "ITR":
        return period.reference + timedelta(days=45)
    return shift_months(period.reference, 3)


def response_version(response: RawFetchResult) -> int:
    return int(response.request.get("version") or response.request["Versao"])
