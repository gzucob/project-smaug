"""Guarded replay of the 13 CAPITAL subclass rows audited in #303."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from smaug.ingestion.domain.ports import (
    ArtifactDataSource,
    RawDataSource,
    RawFetchResult,
)
from smaug.ingestion.infrastructure.cvm_capital import CvmCapitalSource
from smaug.shared.artifacts import SourceArtifact
from smaug.shared.errors import SourceMalformedError


@dataclass(frozen=True, slots=True)
class CapitalSubclassBackfillTarget:
    """One immutable source row and its audited parser-v2 child ledger."""

    ticker: str
    cd_cvm: str
    cnpj: str
    year: int
    artifact_id: str
    reference_date: str
    version: int
    capital_id: str
    approval_date: str
    filing_rows: int
    versions: tuple[int, ...]
    children: tuple[tuple[str, int], ...]

    @property
    def parent_key(self) -> tuple[str, str, int, str]:
        """The complete parent/child join key filed by CVM."""
        return self.cnpj, self.reference_date, self.version, self.capital_id


_FRE_2014 = "sha256:8797e5b66f4c126d9266746ab704bdb0d787ed5d92dca917ceffa2749b150a50"
_FRE_2020 = "sha256:17d2d911201f64dc8ff501104ce8fe343cd4ab3bdb41ea03eb6bfc2c4f6b80ab"
_FRE_2021 = "sha256:882e4c19c0719b7ac75f8e906c264fdd69b899cf0a2942ad00c4eb14a1b855bd"


def _target(
    ticker: str,
    cd_cvm: str,
    cnpj: str,
    year: int,
    artifact_id: str,
    reference_date: str,
    version: int,
    capital_id: str,
    approval_date: str,
    children: tuple[tuple[str, int], ...],
    *,
    filing_rows: int = 1,
) -> CapitalSubclassBackfillTarget:
    return CapitalSubclassBackfillTarget(
        ticker=ticker,
        cd_cvm=cd_cvm,
        cnpj=cnpj,
        year=year,
        artifact_id=artifact_id,
        reference_date=reference_date,
        version=version,
        capital_id=capital_id,
        approval_date=approval_date,
        filing_rows=filing_rows,
        versions=(version,),
        children=children,
    )


AUDITED_CAPITAL_SUBCLASS_TARGETS: tuple[CapitalSubclassBackfillTarget, ...] = (
    _target(
        "CRPG5",
        "11398",
        "15.115.504/0001-24",
        2020,
        _FRE_2020,
        "2020-01-01",
        3,
        "224637",
        "2019-04-29",
        (
            ("Preferencial Classe A", 12_342_238),
            ("Preferencial Classe B", 6_518_111),
        ),
    ),
    _target(
        "CRPG5",
        "11398",
        "15.115.504/0001-24",
        2021,
        _FRE_2021,
        "2021-01-01",
        1,
        "235451",
        "2019-04-29",
        (
            ("Preferencial Classe A", 12_342_238),
            ("Preferencial Classe B", 6_518_111),
        ),
    ),
    _target(
        "BRSR5",
        "1210",
        "92.702.067/0001-96",
        2020,
        _FRE_2020,
        "2020-01-01",
        14,
        "233019",
        "2019-04-25",
        (
            ("Preferencial Classe A", 1_373_091),
            ("Preferencial Classe B", 202_536_545),
        ),
    ),
    _target(
        "BRSR5",
        "1210",
        "92.702.067/0001-96",
        2021,
        _FRE_2021,
        "2021-01-01",
        15,
        "262932",
        "2019-04-25",
        (
            ("Preferencial Classe A", 1_373_091),
            ("Preferencial Classe B", 202_536_545),
        ),
    ),
    _target(
        "SNSY5",
        "12696",
        "14.807.945/0001-24",
        2020,
        _FRE_2020,
        "2020-01-01",
        10,
        "230409",
        "2020-07-27",
        (
            ("Preferencial Classe A", 5_052_280),
            ("Preferencial Classe B", 6_200),
        ),
    ),
    _target(
        "SNSY5",
        "12696",
        "14.807.945/0001-24",
        2021,
        _FRE_2021,
        "2021-01-01",
        3,
        "257781",
        "2020-07-27",
        (
            ("Preferencial Classe A", 5_052_280),
            ("Preferencial Classe B", 6_200),
        ),
    ),
    _target(
        "CEBR5",
        "14451",
        "00.070.698/0001-11",
        2014,
        _FRE_2014,
        "2014-01-01",
        15,
        "103735",
        "2005-02-15",
        (
            ("Preferencial Classe A", 1_313_002),
            ("Preferencial Classe B", 3_294_024),
        ),
    ),
    _target(
        "CGAS5",
        "15636",
        "61.856.571/0001-17",
        2020,
        _FRE_2020,
        "2020-01-01",
        6,
        "233448",
        "2019-07-01",
        (("Preferencial Classe A", 28_657_819),),
    ),
    _target(
        "CGAS5",
        "15636",
        "61.856.571/0001-17",
        2021,
        _FRE_2021,
        "2021-01-01",
        6,
        "262678",
        "2019-07-01",
        (("Preferencial Classe A", 28_657_819),),
    ),
    _target(
        "HBTS5",
        "3298",
        "87.762.563/0001-03",
        2020,
        _FRE_2020,
        "2020-01-01",
        5,
        "231726",
        "2005-04-26",
        (
            ("Preferencial Classe A", 5_950_327),
            ("Preferencial Classe B", 30_596),
        ),
    ),
    _target(
        "HBTS5",
        "3298",
        "87.762.563/0001-03",
        2021,
        _FRE_2021,
        "2021-01-01",
        3,
        "248823",
        "2005-04-26",
        (
            ("Preferencial Classe A", 5_950_327),
            ("Preferencial Classe B", 30_596),
        ),
    ),
    _target(
        "RPAD5",
        "9954",
        "17.167.396/0001-69",
        2020,
        _FRE_2020,
        "2020-01-01",
        3,
        "228292",
        "2021-03-31",
        (
            ("Preferencial Classe A", 14_313_881),
            ("Preferencial Classe B", 24_356_756),
        ),
        filing_rows=5,
    ),
    _target(
        "RPAD5",
        "9954",
        "17.167.396/0001-69",
        2021,
        _FRE_2021,
        "2021-01-01",
        2,
        "257049",
        "2022-03-30",
        (
            ("Preferencial Classe A", 14_313_881),
            ("Preferencial Classe B", 24_356_756),
        ),
        filing_rows=4,
    ),
)


def targets_by_year() -> dict[int, tuple[CapitalSubclassBackfillTarget, ...]]:
    """Group the fixed audit manifest by immutable FRE archive."""
    grouped: dict[int, list[CapitalSubclassBackfillTarget]] = {}
    for target in AUDITED_CAPITAL_SUBCLASS_TARGETS:
        grouped.setdefault(target.year, []).append(target)
    return {year: tuple(targets) for year, targets in grouped.items()}


class AuditedCapitalSubclassSource:
    """Expose only audited rows after reproducing every #303 source invariant."""

    parser_identity = CvmCapitalSource.parser_identity
    source = CvmCapitalSource.source

    def __init__(
        self,
        source: RawDataSource,
        targets: Sequence[CapitalSubclassBackfillTarget],
    ) -> None:
        self._source = source
        self._targets = {target.ticker: target for target in targets}

    async def artifact(self) -> SourceArtifact | None:
        """Expose the exact replay artifact to the normal ingestion lifecycle."""
        if not isinstance(self._source, ArtifactDataSource):
            return None
        return await self._source.artifact()

    async def fetch(self, ticker: str, module: str) -> Sequence[RawFetchResult]:
        """Validate the full registrant/year group, then return its audited row."""
        target = self._targets.get(ticker)
        if target is None:
            raise SourceMalformedError(f"ticker {ticker} is outside #303 manifest")
        rows = await self._source.fetch(ticker, module)
        self._validate_group(target, rows)
        selected = [row for row in rows if self._matches(target, row.payload)]
        if len(selected) != 1:
            raise SourceMalformedError(
                f"#303 drift for {target.cd_cvm}/{target.year}: "
                f"expected one parent row, found {len(selected)}"
            )
        self._validate_selected(target, selected[0])
        return selected

    @staticmethod
    def _validate_group(
        target: CapitalSubclassBackfillTarget, rows: Sequence[RawFetchResult]
    ) -> None:
        versions = tuple(
            sorted(
                {
                    version
                    for row in rows
                    if isinstance((version := row.payload.get("version")), int)
                }
            )
        )
        if len(rows) != target.filing_rows or versions != target.versions:
            raise SourceMalformedError(
                f"#303 drift for {target.cd_cvm}/{target.year}: "
                f"expected rows={target.filing_rows} versions={target.versions}, "
                f"found rows={len(rows)} versions={versions}"
            )

    @staticmethod
    def _matches(
        target: CapitalSubclassBackfillTarget, payload: Mapping[str, Any]
    ) -> bool:
        key = (
            payload.get("cnpj"),
            payload.get("reference_date"),
            payload.get("version"),
            payload.get("capital_id"),
        )
        return key == target.parent_key and (
            payload.get("approval_date") == target.approval_date
        )

    @staticmethod
    def _validate_selected(
        target: CapitalSubclassBackfillTarget, row: RawFetchResult
    ) -> None:
        raw_children = row.payload.get("share_class_counts")
        if not isinstance(raw_children, Sequence) or isinstance(
            raw_children, (str, bytes, bytearray)
        ):
            children: tuple[tuple[str, int], ...] = ()
        else:
            children = tuple(
                sorted(
                    (
                        str(child.get("share_class", "")),
                        int(child.get("shares", 0)),
                    )
                    for child in raw_children
                    if isinstance(child, Mapping)
                )
            )
        expected = tuple(sorted(target.children))
        if row.artifact_id != target.artifact_id or children != expected:
            raise SourceMalformedError(
                f"#303 drift for {target.cd_cvm}/{target.year}: "
                f"expected artifact={target.artifact_id} children={expected}, "
                f"found artifact={row.artifact_id} children={children}"
            )
