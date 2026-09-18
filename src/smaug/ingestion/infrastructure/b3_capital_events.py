"""Corporate actions as **B3** publishes them — the exchange's half of the record.

CVM declares the same events (``CvmCapitalEventSource``) with the share count on
both sides of the approval, which is what lets the analysis anchor a ratio on a
filed count. It stops after the 2023 FRE, where CVM restructured the form and
dropped the member. B3's ``GetListedSupplementCompany`` carries no counts at all
— and carries the one thing CVM never files:

    {'factor': '100,00000000000', 'approvedOn': '02/02/2024',
     'label': 'DESDOBRAMENTO', 'lastDatePrior': '15/04/2024', ...}

``lastDatePrior`` is **the last session quoted on the old share base**. CVM files
the approval, which precedes the market's repricing by weeks — BBAS3's 2024 split
was approved on 2 February and traded split from 16 April. A price series has to
be cut on the second date, not the first (ADR 0033).

The two sources are complementary, not redundant, and neither is a superset:
B3 lists **one** Bradesco bonus (2022) where CVM's file lists its 10% bonus in
2013, 2015, 2016, 2017, 2018, 2019, 2020, 2021 *and* 2022; CVM has nothing after
2023, where B3 has BBAS3's split and VIVT3's composite action.

Mirrored as filed, in B3's own vocabulary and number format (``7.900,00000000000``
is pt-BR for 7900) — reading a percentage out of ``factor`` is an interpretation,
and interpretation is Phase 2's (ADR 0016). The request and result record
``source="b3"`` because B3 published the payload; the CVM code below identifies
the registrant and does not change its provenance (ADR 0055).

A trading root is not permanent identity. When a former root no longer answers,
the source follows the composition root's CVM registrant code through B3
``GetDetail`` to the current root, then requires the current supplement to
confirm that same registrant before admitting any row (ADR 0056).
"""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from decimal import Decimal, InvalidOperation
from typing import Any, Never

import httpx

from smaug.ingestion.domain.ports import RawFetchResult
from smaug.ingestion.domain.runs import ParserIdentity
from smaug.ingestion.domain.validation import (
    BatchValidationReporter,
    SourceBatchValidation,
    ValidationFinding,
    ValidationRule,
)
from smaug.ingestion.infrastructure.b3_listed_company import (
    B3CompanyResolutionError,
    B3ListedCompanyResolver,
)
from smaug.ingestion.infrastructure.b3_reused_roots import (
    B3ReusedRootProof,
    B3ReusedRootRecovery,
)
from smaug.ingestion.infrastructure.batch_validation import record_or_quarantine
from smaug.shared.errors import SourceNotFoundError

B3_LISTED_BASE_URL = (
    "https://sistemaswebb3-listados.b3.com.br/listedCompaniesProxy/CompanyCall"
)

# The exchange's declared corporate actions, keyed on the trading root.
CAPITAL_EVENT_B3_MODULE = "CAPITAL_EVENT_B3"

_RULES = (
    ValidationRule("response-schema", 1),
    ValidationRule("coverage-established", 2),
    ValidationRule("record-count", 1),
    ValidationRule("row-reconciliation", 1),
)


class B3CapitalEventSource:
    """Fetch the corporate actions B3 publishes for one ticker's company."""

    source = "b3"
    parser_identity = ParserIdentity("b3.capital-events.json", 3)

    def __init__(
        self,
        http_client: httpx.AsyncClient,
        *,
        ticker_to_code: Mapping[str, str] | None = None,
        base_url: str | None = None,
        validation_reporter: BatchValidationReporter | None = None,
        reused_root_recovery: B3ReusedRootRecovery | None = None,
    ) -> None:
        self._ticker_to_code = {
            ticker.upper().strip(): code
            for ticker, code in (ticker_to_code or {}).items()
        }
        self._base_url = (base_url or B3_LISTED_BASE_URL).rstrip("/")
        self._companies = B3ListedCompanyResolver(http_client, base_url=self._base_url)
        self._validation_reporter = validation_reporter
        self._reused_root_recovery = reused_root_recovery

    async def fetch(self, ticker: str, module: str) -> Sequence[RawFetchResult]:
        """Every stock-dividend row B3 lists for the company behind ``ticker``.

        One row per event **per ISIN**, which is how B3 files it — BBAS3's split
        appears three times, once for each asset code the company has had. Kept
        as they come: which rows are one event is the reader's judgement, and the
        mirror does not make it (ADR 0016).
        """
        proof: B3ReusedRootProof | None = None
        try:
            company = await self._companies.resolve(
                ticker,
                cvm_code=self._ticker_to_code.get(ticker.upper().strip()),
            )
        except B3CompanyResolutionError as exc:
            normalized_ticker = ticker.strip().upper()
            recovery = self._reused_root_recovery
            if recovery is None or not recovery.supports(normalized_ticker):
                await self._quarantine(
                    ticker[:4].upper(),
                    exc.code,
                    exc.detail,
                    evidence=exc.evidence,
                )
            try:
                company = await self._companies.resolve_current(normalized_ticker)
            except B3CompanyResolutionError as current_exc:
                await self._quarantine(
                    ticker[:4].upper(),
                    current_exc.code,
                    current_exc.detail,
                    evidence={
                        "predecessor_resolution": dict(exc.evidence),
                        "current_resolution": dict(current_exc.evidence),
                    },
                )
            proof_result = await recovery.prove(normalized_ticker, company)
            if proof_result.proof is None:
                await self._quarantine(
                    ticker[:4].upper(),
                    "coverage-established",
                    "B3 reused-root predecessor cannot be proven: "
                    f"{proof_result.reason or 'unknown reason'}",
                    evidence={
                        "predecessor_resolution": dict(exc.evidence),
                        "current_company": dict(company.supplement),
                        "recovery": dict(proof_result.evidence),
                    },
                )
            proof = proof_result.proof
        root = company.requested_root
        issuing_company = company.issuing_company
        body = company.supplement
        rows = body.get("stockDividends")
        if not isinstance(rows, list):
            await self._quarantine(
                root,
                "response-schema",
                "B3 supplement lacks a stockDividends list",
                evidence=body,
            )
        if not rows:
            if proof is not None:
                await self._quarantine(
                    root,
                    "coverage-established",
                    "B3 reused-root predecessor has no attributable corporate-action "
                    "rows in the current supplement",
                    evidence={"reused_root": proof.as_mapping(), "supplement": body},
                )
            # A company with no corporate action in its history is the normal
            # case, and it is an absence the mirror records rather than an empty
            # list it invents.
            await self._record(self._validation(root, rows=0))
            raise SourceNotFoundError(f"B3 lists no corporate action for {ticker}")
        admitted_rows, rejected_rows, duplicate_rows, conflicting_rows = (
            _reconcile_rows(rows)
        )
        fetched = len(rows)
        evidence: dict[str, object] = {}
        if rejected_rows:
            evidence["rejected_rows"] = rejected_rows
        if duplicate_rows:
            evidence["deduplicated_rows"] = duplicate_rows
        if conflicting_rows:
            evidence["conflicting_rows"] = conflicting_rows
        findings: list[ValidationFinding] = []
        if rejected_rows:
            findings.append(
                ValidationFinding(
                    "row-reconciliation",
                    f"B3 stock events rejected {len(rejected_rows)} row(s); "
                    "the batch is not admitted",
                )
            )
        if conflicting_rows:
            findings.append(
                ValidationFinding(
                    "row-reconciliation",
                    f"B3 stock events found {len(conflicting_rows)} "
                    "conflicting economic group(s); the batch is not admitted",
                )
            )
        recovery_evidence: dict[str, object] = {}
        if proof is not None:
            assert self._reused_root_recovery is not None
            admitted_rows = []
            excluded_rows: list[dict[str, object]] = []
            for row_number, row in enumerate(rows, start=1):
                if not isinstance(row, dict):
                    continue
                decision = await self._reused_root_recovery.capital_event(proof, row)
                if decision.accepted:
                    admitted_rows.append(row)
                else:
                    excluded_rows.append(
                        {
                            "row": row_number,
                            "reason": decision.reason,
                            "evidence": dict(decision.evidence),
                            "raw": row,
                        }
                    )
            if not admitted_rows:
                await self._quarantine(
                    root,
                    "coverage-established",
                    "B3 reused-root recovery could not attribute any corporate-action "
                    "row to the predecessor",
                    evidence={
                        "reused_root": proof.as_mapping(),
                        "excluded_rows": excluded_rows,
                    },
                )
            recovery_evidence = {
                "reused_root": proof.as_mapping(),
                "excluded_rows": excluded_rows,
            }
        conflicting_count = 0
        for item in conflicting_rows:
            rows_in_group = item.get("rows")
            if isinstance(rows_in_group, list):
                conflicting_count += len(rows_in_group)
        await self._record(
            self._validation(
                root,
                rows=len(rows),
                fetched=fetched,
                accepted=len(admitted_rows),
                rejected=len(rejected_rows),
                deduplicated=len(duplicate_rows),
                conflicting=conflicting_count,
                coverage_established=not findings,
                findings=tuple(findings),
                evidence={**evidence, **recovery_evidence},
            )
        )

        code = proof.predecessor_cvm_code if proof is not None else company.cvm_code
        results: list[RawFetchResult] = []
        for row in admitted_rows:
            request: dict[str, Any] = {
                "source": "b3",
                "endpoint": "GetListedSupplementCompany",
                "issuing_company": issuing_company,
                "statement": module,
                # Keep every source field in the request discriminator: an
                # amendment to a published row is a distinct raw fact.
                "b3_row": dict(row),
                # What tells one filed row from another: the same event is
                # listed once per ISIN, and one approval date can carry two
                # events (VIVT3's split and grupamento, 2025-03-13).
                "isin_code": _text(row.get("isinCode")),
                "approval_date": _text(row.get("approvedOn")),
                "event_type": _text(row.get("label")),
            }
            payload = _to_payload(row, issuing_company, code)
            if proof is not None:
                identity = proof.as_mapping()
                request["historical_identity"] = identity
                payload["historical_identity"] = identity
            results.append(
                RawFetchResult(
                    module=module,
                    source="b3",
                    request=request,
                    http_status=200,
                    payload=payload,
                    cvm_code=code,
                )
            )
        return results

    def _validation(
        self,
        root: str,
        *,
        rows: int,
        fetched: int | None = None,
        accepted: int | None = None,
        rejected: int = 0,
        deduplicated: int = 0,
        conflicting: int = 0,
        coverage_established: bool | None = None,
        findings: tuple[ValidationFinding, ...] = (),
        evidence: Mapping[str, object] | None = None,
    ) -> SourceBatchValidation:
        fetched_count = rows if fetched is None else fetched
        accepted_count = (
            rows - rejected - deduplicated if accepted is None else accepted
        )
        return SourceBatchValidation(
            source="b3",
            batch=f"GetListedSupplementCompany:{root}",
            module=CAPITAL_EVENT_B3_MODULE,
            parser=self.parser_identity,
            rules=_RULES,
            observations={
                "rows": rows,
                "fetched": fetched_count,
                "accepted": accepted_count,
                "rejected": rejected,
                "deduplicated": deduplicated,
                "conflicting": conflicting,
                "coverage_established": (
                    not findings
                    if coverage_established is None
                    else coverage_established
                ),
            },
            findings=findings,
            evidence=evidence or {},
        )

    async def _record(self, validation: SourceBatchValidation) -> None:
        await record_or_quarantine(self._validation_reporter, validation)

    async def _quarantine(
        self,
        root: str,
        code: str,
        detail: str,
        *,
        evidence: Mapping[str, object] | None = None,
    ) -> Never:
        await self._record(
            self._validation(
                root,
                rows=0,
                findings=(ValidationFinding(code, detail),),
                evidence=evidence,
            )
        )
        raise AssertionError("quarantined batch returned unexpectedly")


def _to_payload(row: Mapping[str, Any], root: str, code: str | None) -> dict[str, Any]:
    """Mirror the row as B3 publishes it — its vocabulary, its number format."""
    payload = {str(key): value for key, value in row.items()}
    payload.update(
        {
            "issuing_company": root,
            "cvm_code": code,
            "isin_code": _text(row.get("isinCode")),
            "asset_issued": _text(row.get("assetIssued")),
            "event_type": _text(row.get("label")),
            # pt-BR, and a percentage for a split/bonus but a multiplier for a
            # grupamento. Stored as the string B3 sends: the two readings are the
            # analysis context's problem, not the mirror's.
            "factor": _text(row.get("factor")),
            "approval_date": _text(row.get("approvedOn")),
            # The last session quoted on the old base — the cut a price series needs.
            "last_date_prior": _text(row.get("lastDatePrior")),
            "remarks": _text(row.get("remarks")),
        }
    )
    return payload


def _text(value: Any) -> str:
    return str(value).strip() if value is not None else ""


_REQUIRED_ROW_FIELDS = frozenset(
    {"isinCode", "assetIssued", "approvedOn", "label", "factor", "lastDatePrior"}
)


def _reconcile_rows(
    rows: Sequence[object],
) -> tuple[
    list[Mapping[str, Any]],
    list[dict[str, object]],
    list[dict[str, object]],
    list[dict[str, object]],
]:
    """Reconcile B3 rows without collapsing the raw per-ISIN history.

    The economic identity is ``(approvedOn, label, factor)``. B3 repeats that
    identity once per ISIN, so ``isinCode`` and ``assetIssued`` are deliberately
    not amendment fields. An amendment is identified by the same economic key
    plus ``lastDatePrior`` and ``remarks``; two amendment identities for one
    economic key are unsafe because they would date the price cut differently.
    Exact source duplicates are dropped before the raw mirror is written, while
    distinct per-ISIN rows remain accepted and are reconciled by analysis.
    """
    accepted: list[Mapping[str, Any]] = []
    rejected: list[dict[str, object]] = []
    deduplicated: list[dict[str, object]] = []
    groups: dict[tuple[str, str, str], list[tuple[int, Mapping[str, Any]]]] = {}
    source_identities: dict[str, int] = {}

    for row_number, row in enumerate(rows, start=1):
        finding = _row_finding(row, row_number)
        if finding is not None:
            rejected.append(
                {
                    "row": row_number,
                    "finding": {
                        "code": finding.code,
                        "detail": finding.detail,
                    },
                    "raw": row,
                }
            )
            continue

        assert isinstance(row, Mapping)
        source_identity = _source_row_identity(row)
        first_row = source_identities.get(source_identity)
        if first_row is not None:
            deduplicated.append(
                {
                    "row": row_number,
                    "matches": first_row,
                    "identity": _identity_evidence(row),
                    "source_identity": source_identity,
                }
            )
            continue

        source_identities[source_identity] = row_number
        accepted.append(row)
        groups.setdefault(_economic_identity(row), []).append((row_number, row))

    conflicting: list[dict[str, object]] = []
    for _identity, members in groups.items():
        amendment_identities = {
            _amendment_identity(row) for _row_number, row in members
        }
        if len(amendment_identities) <= 1:
            continue
        conflicting.append(
            {
                "identity": _identity_evidence(members[0][1]),
                "amendments": [
                    {
                        "last_date_prior": last_date_prior,
                        "remarks": remarks,
                    }
                    for last_date_prior, remarks in sorted(amendment_identities)
                ],
                "rows": [
                    {
                        "row": row_number,
                        "isin_code": _text(row.get("isinCode")),
                        "asset_issued": _text(row.get("assetIssued")),
                        "last_date_prior": _text(row.get("lastDatePrior")),
                        "remarks": _text(row.get("remarks")),
                        "raw": row,
                    }
                    for row_number, row in members
                ],
            }
        )

    return accepted, rejected, deduplicated, conflicting


def _row_finding(row: object, row_number: int) -> ValidationFinding | None:
    if not isinstance(row, Mapping):
        return ValidationFinding(
            "response-schema",
            f"B3 stock event row {row_number} is not an object",
        )
    missing = sorted(field for field in _REQUIRED_ROW_FIELDS if field not in row)
    if missing:
        return ValidationFinding(
            "response-schema",
            f"B3 stock event row {row_number} lacks {', '.join(missing)}",
        )
    blank = sorted(field for field in _REQUIRED_ROW_FIELDS if not _text(row[field]))
    if blank:
        return ValidationFinding(
            "response-schema",
            f"B3 stock event row {row_number} has empty {', '.join(blank)}",
        )
    return None


def _economic_identity(row: Mapping[str, Any]) -> tuple[str, str, str]:
    """Identify one economic B3 action, independent of its listed security."""
    return (
        _date_identity(row.get("approvedOn")),
        _text(row.get("label")).upper(),
        _number_identity(row.get("factor")),
    )


def _amendment_identity(row: Mapping[str, Any]) -> tuple[str, str]:
    """Identify the fields whose change can move a B3 action's effective date."""
    return (
        _date_identity(row.get("lastDatePrior")),
        _text(row.get("remarks")),
    )


def _identity_evidence(row: Mapping[str, Any]) -> dict[str, str]:
    approval_date, kind, factor = _economic_identity(row)
    last_date_prior, remarks = _amendment_identity(row)
    return {
        "approval_date": approval_date,
        "event_type": kind,
        "factor": factor,
        "last_date_prior": last_date_prior,
        "remarks": remarks,
    }


def _source_row_identity(row: Mapping[str, Any]) -> str:
    """Identify an exact source row without discarding amended B3 fields."""
    return json.dumps(
        {str(key): value for key, value in row.items()},
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
        default=str,
    )


def _date_identity(value: Any) -> str:
    """Canonicalize B3's ``DD/MM/YYYY`` date while retaining bad raw values."""
    raw = _text(value)
    parts = raw.split("/")
    if len(parts) != 3:
        return raw
    try:
        day, month, year = (int(part) for part in parts)
    except ValueError:
        return raw
    if not (1 <= month <= 12 and 1 <= day <= 31 and year > 0):
        return raw
    return f"{year:04d}-{month:02d}-{day:02d}"


def _number_identity(value: Any) -> str:
    """Canonicalize a B3 pt-BR number without changing the mirrored spelling."""
    raw = _text(value)
    try:
        parsed = Decimal(raw.replace(".", "").replace(",", "."))
    except InvalidOperation:
        return raw
    return format(parsed.normalize(), "f")
