"""Reusable CVM archive shape checks performed before parsing a source batch."""

from __future__ import annotations

import csv
import io
import zipfile
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass
from pathlib import Path

from smaug.ingestion.domain.runs import ParserIdentity
from smaug.ingestion.domain.validation import (
    BatchValidationReporter,
    SourceBatchValidation,
    ValidationFinding,
    ValidationRule,
)
from smaug.shared.artifacts import SourceArtifact
from smaug.shared.errors import SourceBatchValidationError

_ENCODING = "latin-1"
_DELIMITER = ";"
_ARCHIVE_RULES = (
    ValidationRule("archive-integrity", 1),
    ValidationRule("required-members", 1),
    ValidationRule("csv-schema", 1),
    ValidationRule("supported-period", 1),
    ValidationRule("registrant-keys", 1),
    ValidationRule("record-count", 1),
)


@dataclass(frozen=True)
class CsvMemberSpec:
    """The source contract for one CSV member in a CVM archive."""

    name: str
    required_columns: frozenset[str]
    registrant_column: str
    period_column: str


def validate_csv_archive(
    archive_path: Path,
    *,
    source: str,
    batch: str,
    parser: ParserIdentity,
    artifact: SourceArtifact | None,
    expected_year: int,
    members: Sequence[CsvMemberSpec],
    require_member: bool = True,
) -> SourceBatchValidation:
    """Validate ZIP integrity and the schema/coverage facts readers rely on."""
    findings: list[ValidationFinding] = []
    row_count = 0
    registrants: set[str] = set()
    expected_period_seen = False
    member_count = 0
    try:
        with zipfile.ZipFile(archive_path) as archive:
            corrupt_member = archive.testzip()
            if corrupt_member is not None:
                findings.append(
                    ValidationFinding(
                        "archive-integrity", f"corrupt member: {corrupt_member}"
                    )
                )
            names = set(archive.namelist())
            available = tuple(spec for spec in members if spec.name in names)
            member_count = len(available)
            if require_member and not available:
                findings.append(
                    ValidationFinding(
                        "required-members", "no supported CSV member found"
                    )
                )
            for spec in available:
                rows, keys, period_seen, member_findings = _inspect_member(
                    archive, spec, expected_year
                )
                row_count += rows
                registrants.update(keys)
                expected_period_seen = expected_period_seen or period_seen
                findings.extend(member_findings)
    except (OSError, zipfile.BadZipFile) as exc:
        findings.append(ValidationFinding("archive-integrity", str(exc)))

    if member_count and row_count == 0:
        findings.append(
            ValidationFinding("record-count", "CSV members contain no rows")
        )
    if member_count and not registrants:
        findings.append(
            ValidationFinding("registrant-keys", "no non-empty registrant key")
        )
    if member_count and not expected_period_seen:
        findings.append(
            ValidationFinding(
                "supported-period", f"no row for source year {expected_year}"
            )
        )
    return SourceBatchValidation(
        source=source,
        batch=batch,
        parser=parser,
        artifact_id=artifact.artifact_id if artifact is not None else None,
        rules=_ARCHIVE_RULES,
        observations={
            "members": member_count,
            "rows": row_count,
            "registrants": len(registrants),
            "expected_year": expected_year,
            "expected_period_seen": expected_period_seen,
        },
        findings=tuple(findings),
    )


def _inspect_member(
    archive: zipfile.ZipFile,
    spec: CsvMemberSpec,
    expected_year: int,
) -> tuple[int, set[str], bool, list[ValidationFinding]]:
    findings: list[ValidationFinding] = []
    with archive.open(spec.name) as raw:
        reader = csv.DictReader(
            io.TextIOWrapper(raw, encoding=_ENCODING), delimiter=_DELIMITER
        )
        columns = set(reader.fieldnames or ())
        missing = sorted(spec.required_columns - columns)
        if missing:
            findings.append(
                ValidationFinding(
                    "csv-schema", f"{spec.name} lacks {', '.join(missing)}"
                )
            )
            return 0, set(), False, findings
        row_count = 0
        registrants: set[str] = set()
        expected_period_seen = False
        for row in reader:
            row_count += 1
            key = (row.get(spec.registrant_column) or "").strip()
            if key:
                registrants.add(key)
            period = (row.get(spec.period_column) or "").strip()
            expected_period_seen = expected_period_seen or str(expected_year) in period
    return row_count, registrants, expected_period_seen, findings


def statement_members(
    names: Iterable[str],
    classifier: Callable[[str], tuple[str, str] | None],
) -> tuple[CsvMemberSpec, ...]:
    """Describe the statement members a particular CVM archive actually contains."""
    columns = frozenset(
        {
            "CD_CVM",
            "DT_REFER",
            "VERSAO",
            "ORDEM_EXERC",
            "DENOM_CIA",
            "MOEDA",
            "ESCALA_MOEDA",
            "DT_FIM_EXERC",
            "CD_CONTA",
            "DS_CONTA",
            "VL_CONTA",
            "ST_CONTA_FIXA",
        }
    )
    return tuple(
        CsvMemberSpec(name, columns, "CD_CVM", "DT_REFER")
        for name in names
        if classifier(name) is not None
    )


async def record_or_quarantine(
    reporter: BatchValidationReporter | None,
    validation: SourceBatchValidation,
) -> None:
    """Persist a decision before preventing a rejected batch from being parsed."""
    if reporter is not None:
        await reporter.record(validation)
    if validation.findings:
        raise SourceBatchValidationError(validation.detail)


def quarantined_archive_validation(
    *,
    batch: str,
    parser: ParserIdentity,
    artifact_id: str,
    detail: str,
) -> SourceBatchValidation:
    """Turn an archive-store integrity rejection into a durable batch report."""
    return SourceBatchValidation(
        source="cvm",
        batch=batch,
        parser=parser,
        artifact_id=artifact_id,
        rules=(ValidationRule("archive-integrity", 1),),
        findings=(ValidationFinding("archive-integrity", detail),),
    )
