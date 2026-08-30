"""Cash payouts as **B3** publishes them — the exchange's dividend record.

The dividend-adjusted price basis has no published series anywhere: B3's quote
file carries the price as traded and nothing else (ADR 0032), and the vendor
that used to supply it was removed. It is rebuilt from this, which is the
one record complete enough to rebuild it from — 900 rows for Bradesco reaching
back to 1995-12-28, where the same company's *stock* events number one.

Three B3 endpoints form the identity-safe chain:

``GetListedSupplementCompany`` answers on a trading root (``BBDC``) and returns
the ``tradingName`` (``BRADESCO``); ``GetListedCashDividends`` takes only that
name, and is paginated. The supplement carries a ``cashDividends`` list of its
own, and it is *not* the same thing: 32 recent rows against the paginated
endpoint's 900. A former or reused root is resolved through ``GetDetail`` by the
stable CVM registrant code, then back to the current supplement. Each hop must
confirm that same code (ADR 0056).

Mirrored as filed, in B3's vocabulary and pt-BR number format (ADR 0016). Two
fields make that worth insisting on:

- ``quotedPerShares`` is 1 or 1000, the same lot-quotation trap the price file
  carries in ``FATCOT`` — a quarter of Bradesco's rows are per lot of a
  thousand, where ``valueCash`` of ``0,01`` is a hundredth of a *centavo* a
  share.
- ``corporateActionPrice`` is B3's own reading of the payment as a percentage of
  the closing price it went ex against, which is the adjustment factor already
  computed and already free of that scale. It is null on a handful of rows whose
  ``valueCash`` is ``0,0000000001`` — a nominal payment that rounds to nothing.

**The two endpoints spell the corporate form differently.** Every other B3
system writes it with a slash — the supplement, ``GetInitialCompanies``,
``GetDetail`` and COTAHIST's own short name all call Ambev ``AMBEV S/A`` — and
the dividend table writes it with dots, ``AMBEV S.A.``. The match is exact but
for dots, so ``AMBEV SA`` answers and ``AMBEV S/A`` returns nothing at all.
Measured over 371 companies: 50 return nothing on the supplement's name, and
six of those are this and no other cause (Ambev, Klabin, Cury, Light, IMC,
Ourofino). The rest have simply never paid.

The corporate-form retry is a **respelling, not a search**. Truncating a name
until something answers looks like it works and does not: ``KLABIN`` answers
with 18 rows from 1996-2001 and ``KLABIN S.A.`` with 219, because the short name
is a different, dead registrant. The response carries no company identity to
catch that with — its fields are share class, date, value and reference price —
so a wrong name returns a perfectly valid history of somebody else. Only the
two spellings of one registrant-verified name are tried, and nothing else
(ADR 0056).
"""

from __future__ import annotations

import base64
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
from smaug.shared.logging import get_logger

logger = get_logger(__name__)

B3_LISTED_BASE_URL = (
    "https://sistemaswebb3-listados.b3.com.br/listedCompaniesProxy/CompanyCall"
)

# The exchange's cash payouts, keyed on the trading root.
CASH_DIVIDEND_B3_MODULE = "CASH_DIVIDEND_B3"

_USER_AGENT = "Mozilla/5.0"
# The endpoint returns an empty result set above a few hundred, so the history is
# walked rather than asked for whole.
_PAGE_SIZE = 100

# A company paying monthly since the nineties reaches ~900 rows; the bound only
# exists so a malformed ``totalPages`` cannot spin.
_MAX_PAGES = 60
_RULES = (
    ValidationRule("response-schema", 1),
    ValidationRule("coverage-established", 2),
    ValidationRule("record-count", 1),
    ValidationRule("row-reconciliation", 1),
)


class B3CashDividendSource:
    """Fetch every cash payout B3 lists for one ticker's company."""

    source = "b3"
    parser_identity = ParserIdentity("b3.cash-dividends.json", 3)

    def __init__(
        self,
        http_client: httpx.AsyncClient,
        *,
        ticker_to_code: Mapping[str, str] | None = None,
        base_url: str | None = None,
        validation_reporter: BatchValidationReporter | None = None,
        reused_root_recovery: B3ReusedRootRecovery | None = None,
    ) -> None:
        self._http = http_client
        self._ticker_to_code = {
            ticker.upper().strip(): code
            for ticker, code in (ticker_to_code or {}).items()
        }
        self._base_url = (base_url or B3_LISTED_BASE_URL).rstrip("/")
        self._companies = B3ListedCompanyResolver(http_client, base_url=self._base_url)
        self._validation_reporter = validation_reporter
        self._reused_root_recovery = reused_root_recovery

    async def fetch(self, ticker: str, module: str) -> Sequence[RawFetchResult]:
        """Every cash-dividend row B3 lists, one result per row.

        One row per event **per share class** — B3 files a payment once for ON
        and once for PN, at rates that differ. Which rows a ticker reads is the
        analysis context's judgement, not the mirror's.
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
        trading_name = company.trading_name
        rows = await self._dividends(root, trading_name)
        respelled = trading_name.replace("/", ".")
        if not rows and respelled != trading_name:
            logger.info(
                "B3 gave no payout for %r; the dividend table spells it %r",
                trading_name,
                respelled,
            )
            rows = await self._dividends(root, respelled)
            if rows:
                trading_name = respelled
        if not rows:
            if proof is not None:
                await self._quarantine(
                    root,
                    "coverage-established",
                    "B3 reused-root predecessor cash history cannot be proven from "
                    "the current trading-name endpoint",
                    evidence={"reused_root": proof.as_mapping()},
                )
            # A company that has never paid is the normal case for a recent
            # listing, and it is an absence the mirror records rather than an
            # empty list it invents.
            await self._record(self._validation(root, rows=0))
            raise SourceNotFoundError(f"B3 lists no cash payout for {ticker}")

        accepted_rows, rejected_rows, duplicate_rows = _reconcile_rows(rows)
        fetched = len(rows)
        evidence: dict[str, object] = {}
        if rejected_rows:
            evidence["rejected_rows"] = rejected_rows
        if duplicate_rows:
            evidence["deduplicated_rows"] = duplicate_rows
        findings = (
            (
                ValidationFinding(
                    "row-reconciliation",
                    f"B3 dividends rejected {len(rejected_rows)} row(s); "
                    "the batch is not admitted",
                ),
            )
            if rejected_rows
            else ()
        )
        recovery_evidence: dict[str, object] = {}
        if proof is not None:
            assert self._reused_root_recovery is not None
            recovered_rows: list[Mapping[str, Any]] = []
            excluded_rows: list[dict[str, object]] = []
            for row_number, row in enumerate(accepted_rows, start=1):
                decision = await self._reused_root_recovery.cash_dividend(proof, row)
                if decision.accepted:
                    recovered_rows.append(row)
                else:
                    excluded_rows.append(
                        {
                            "row": row_number,
                            "reason": decision.reason,
                            "evidence": dict(decision.evidence),
                            "raw": row,
                        }
                    )
            if not recovered_rows:
                await self._quarantine(
                    root,
                    "coverage-established",
                    "B3 reused-root recovery could not attribute any cash row to "
                    "the predecessor",
                    evidence={
                        "reused_root": proof.as_mapping(),
                        "excluded_rows": excluded_rows,
                    },
                )
            accepted_rows = recovered_rows
            recovery_evidence = {
                "reused_root": proof.as_mapping(),
                "excluded_rows": excluded_rows,
            }
        await self._record(
            self._validation(
                root,
                rows=fetched,
                fetched=fetched,
                accepted=len(accepted_rows),
                rejected=len(rejected_rows),
                deduplicated=len(duplicate_rows),
                coverage_established=not rejected_rows,
                findings=findings,
                evidence={**evidence, **recovery_evidence},
            )
        )

        code = proof.predecessor_cvm_code if proof is not None else company.cvm_code
        results: list[RawFetchResult] = []
        for row in accepted_rows:
            request: dict[str, Any] = {
                "source": "b3",
                "endpoint": "GetListedCashDividends",
                "issuing_company": issuing_company,
                "trading_name": trading_name,
                "statement": module,
                # Keep every source field in the request discriminator: a
                # corrected approval/reference value is a new raw fact.
                "b3_row": dict(row),
                # What tells one filed row from another: the same payment is
                # listed once per class, and one ex date can carry both a
                # dividend and interest on own capital.
                "share_class": _text(row.get("typeStock")),
                "last_date_prior": _text(row.get("lastDatePriorEx")),
                "event_type": _text(row.get("corporateAction")),
                "value": _text(row.get("valueCash")),
                "quoted_per_shares": _text(row.get("quotedPerShares")),
                "approval_date": _text(row.get("dateApproval")),
                "closing_price_prior": _text(row.get("closingPricePriorExDate")),
                "percentage_of_price": _text(row.get("corporateActionPrice")),
                "last_date_time_prior": _text(row.get("lastDateTimePriorEx")),
                "date_closing_price_prior": _text(
                    row.get("dateClosingPricePriorExDate")
                ),
                "ratio": _text(row.get("ratio")),
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

    async def _dividends(self, root: str, trading_name: str) -> list[object]:
        rows: list[object] = []
        page = 1
        while page <= _MAX_PAGES:
            body = await self._json(
                f"{self._base_url}/GetListedCashDividends/"
                + _encoded(
                    {
                        "language": "pt-br",
                        "pageNumber": page,
                        "pageSize": _PAGE_SIZE,
                        "tradingName": trading_name,
                    }
                ),
                root,
            )
            if not isinstance(body, dict):
                await self._quarantine(
                    root,
                    "response-schema",
                    "B3 dividends response is not a JSON object",
                    evidence={"response": body},
                )
            results = body.get("results")
            page_data = body.get("page")
            pages = (
                page_data.get("totalPages") if isinstance(page_data, Mapping) else None
            )
            returned_page = (
                page_data.get("pageNumber") if isinstance(page_data, Mapping) else None
            )
            if (
                not isinstance(results, list)
                or not isinstance(pages, int)
                or not isinstance(returned_page, int)
                or pages < 0
                or returned_page != page
            ):
                await self._quarantine(
                    root,
                    "response-schema",
                    "B3 dividends response lacks a matching page contract",
                    evidence=body,
                )
            if not results:
                if page < pages:
                    await self._quarantine(
                        root,
                        "coverage-established",
                        f"B3 returned an empty page {page} before page {pages}",
                        evidence=body,
                    )
                return rows
            if page > pages:
                await self._quarantine(
                    root,
                    "coverage-established",
                    f"B3 returned rows on page {page} beyond page {pages}",
                    evidence=body,
                )
            rows.extend(results)
            if page >= pages:
                break
            page += 1
        if page > _MAX_PAGES:
            await self._quarantine(
                root,
                "coverage-established",
                f"B3 pagination exceeds {_MAX_PAGES} pages",
            )
        return rows

    async def _json(self, url: str, root: str) -> Any:
        try:
            response = await self._http.get(
                url, headers={"User-Agent": _USER_AGENT}, timeout=30.0
            )
        except httpx.HTTPError as exc:
            logger.warning("B3 dividend call failed: %s", exc)
            await self._quarantine(root, "coverage-established", str(exc))
        if response.status_code != httpx.codes.OK or not response.text.strip():
            await self._quarantine(
                root,
                "coverage-established",
                f"B3 returned HTTP {response.status_code} or an empty body",
            )
        try:
            return response.json()
        except ValueError:
            logger.warning("B3 dividend call did not return JSON: %s", url)
            await self._quarantine(root, "response-schema", "B3 response is not JSON")

    def _validation(
        self,
        root: str,
        *,
        rows: int,
        fetched: int | None = None,
        accepted: int | None = None,
        rejected: int = 0,
        deduplicated: int = 0,
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
            batch=f"GetListedCashDividends:{root}",
            module=CASH_DIVIDEND_B3_MODULE,
            parser=self.parser_identity,
            rules=_RULES,
            observations={
                "rows": rows,
                "fetched": fetched_count,
                "accepted": accepted_count,
                "rejected": rejected,
                "deduplicated": deduplicated,
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
            "share_class": _text(row.get("typeStock")),
            "event_type": _text(row.get("corporateAction")),
            "value": _text(row.get("valueCash")),
            # 1 or 1000: the payment and the reference price are both quoted on it.
            "quoted_per_shares": _text(row.get("quotedPerShares")),
            "approval_date": _text(row.get("dateApproval")),
            # The last session that still carried the right to the payment.
            "last_date_prior": _text(row.get("lastDatePriorEx")),
            "closing_price_prior": _text(row.get("closingPricePriorExDate")),
            # B3's own reading of the payment as a percentage of that close.
            "percentage_of_price": _text(row.get("corporateActionPrice")),
            # Keep identity-bearing variants that are present on some B3 revisions.
            "last_date_time_prior": _text(row.get("lastDateTimePriorEx")),
            "date_closing_price_prior": _text(row.get("dateClosingPricePriorExDate")),
            "ratio": _text(row.get("ratio")),
        }
    )
    return payload


def _text(value: Any) -> str:
    return str(value).strip() if value is not None else ""


_REQUIRED_ROW_FIELDS = frozenset(
    {
        "typeStock",
        "lastDatePriorEx",
        "corporateAction",
        "valueCash",
        "quotedPerShares",
    }
)


def _reconcile_rows(
    rows: Sequence[object],
) -> tuple[
    list[Mapping[str, Any]],
    list[dict[str, object]],
    list[dict[str, object]],
]:
    """Classify source rows before they become raw mirror records.

    B3 occasionally repeats a payment while walking the paginated endpoint. The
    canonical source identity includes every published field, so an amended
    approval or reference price remains a distinct raw fact. The separate
    economic identity used by the analysis reader intentionally excludes those
    display-only fields. Rows that cannot carry either identity are retained in
    validation evidence, never silently interpreted as an empty history.
    """
    accepted: list[Mapping[str, Any]] = []
    rejected: list[dict[str, object]] = []
    deduplicated: list[dict[str, object]] = []
    first_by_identity: dict[str, int] = {}

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
        identity = _source_row_identity(row)
        first_row = first_by_identity.get(identity)
        if first_row is not None:
            deduplicated.append(
                {
                    "row": row_number,
                    "matches": first_row,
                    "identity": _identity_evidence(row),
                    "source_identity": identity,
                }
            )
            continue
        first_by_identity[identity] = row_number
        accepted.append(row)

    return accepted, rejected, deduplicated


def _row_finding(row: object, row_number: int) -> ValidationFinding | None:
    if not isinstance(row, Mapping):
        return ValidationFinding(
            "response-schema",
            f"B3 dividends row {row_number} is not an object",
        )
    missing = sorted(field for field in _REQUIRED_ROW_FIELDS if field not in row)
    if missing:
        return ValidationFinding(
            "response-schema",
            f"B3 dividends row {row_number} lacks {', '.join(missing)}",
        )
    blank = sorted(field for field in _REQUIRED_ROW_FIELDS if not _text(row[field]))
    if blank:
        return ValidationFinding(
            "response-schema",
            f"B3 dividends row {row_number} has empty {', '.join(blank)}",
        )
    return None


def _economic_identity(row: Mapping[str, Any]) -> tuple[str, ...]:
    """Return the B3 fields that identify one economic cash right."""
    return (
        _text(row.get("typeStock")).upper(),
        _text(row.get("corporateAction")).upper(),
        _text(row.get("lastDatePriorEx")),
        _number_identity(row.get("valueCash")),
        _number_identity(row.get("quotedPerShares")),
    )


def _source_row_identity(row: Mapping[str, Any]) -> str:
    """Identify an exact source row without discarding amended B3 fields.

    Economic fields are enough for the analysis reader to avoid paying one right
    twice, but they are not enough for the raw mirror: a changed approval date,
    reference close or corporate-action percentage is a distinct source fact and
    must remain available for audit and replay.
    """
    return json.dumps(
        {str(key): value for key, value in row.items()},
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
        default=str,
    )


def _identity_evidence(row: Mapping[str, Any]) -> dict[str, str]:
    """Render an economic identity with source vocabulary for audit reports."""
    identity = _economic_identity(row)
    return {
        "share_class": identity[0],
        "event_type": identity[1],
        "last_date_prior": identity[2],
        "value": identity[3],
        "quoted_per_shares": identity[4],
    }


def _number_identity(value: object) -> str:
    """Canonicalize B3's pt-BR number spelling without changing its raw payload."""
    raw = _text(value)
    try:
        parsed = Decimal(raw.replace(".", "").replace(",", "."))
    except InvalidOperation:
        return raw
    return format(parsed.normalize(), "f")


def _encoded(params: dict[str, object]) -> str:
    """B3's proxy takes its parameters as base64-encoded JSON in the path."""
    return base64.b64encode(json.dumps(params).encode()).decode()
