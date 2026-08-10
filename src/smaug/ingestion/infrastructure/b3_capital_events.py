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
and interpretation is Phase 2's (ADR 0016). The request records ``"source": "b3"``
even though the run that fetched it is a CVM run: the module name and that key are
what say where the row came from.
"""

from __future__ import annotations

import base64
import json
from collections.abc import Mapping, Sequence
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
from smaug.ingestion.infrastructure.batch_validation import record_or_quarantine
from smaug.shared.errors import SourceNotFoundError
from smaug.shared.logging import get_logger

logger = get_logger(__name__)

B3_LISTED_BASE_URL = (
    "https://sistemaswebb3-listados.b3.com.br/listedCompaniesProxy/CompanyCall"
)

# The exchange's declared corporate actions, keyed on the trading root.
CAPITAL_EVENT_B3_MODULE = "CAPITAL_EVENT_B3"

# The endpoint refuses a request that does not look like it came from a browser.
_USER_AGENT = "Mozilla/5.0"

# A B3 trading root is the ticker's first four characters — the class digit is
# what is left over (``PETR4`` -> ``PETR``, ``B3SA3`` -> ``B3SA``). Not "the
# letters": a root can carry a digit of its own.
_ROOT_LENGTH = 4
_RULES = (
    ValidationRule("response-schema", 1),
    ValidationRule("coverage-established", 1),
    ValidationRule("record-count", 1),
)


class B3CapitalEventSource:
    """Fetch the corporate actions B3 publishes for one ticker's company."""

    parser_identity = ParserIdentity("b3.capital-events.json", 1)

    def __init__(
        self,
        http_client: httpx.AsyncClient,
        *,
        ticker_to_code: Mapping[str, str] | None = None,
        base_url: str | None = None,
        validation_reporter: BatchValidationReporter | None = None,
    ) -> None:
        self._http = http_client
        self._ticker_to_code = dict(ticker_to_code or {})
        self._base_url = (base_url or B3_LISTED_BASE_URL).rstrip("/")
        self._validation_reporter = validation_reporter

    async def fetch(self, ticker: str, module: str) -> Sequence[RawFetchResult]:
        """Every stock-dividend row B3 lists for the company behind ``ticker``.

        One row per event **per ISIN**, which is how B3 files it — BBAS3's split
        appears three times, once for each asset code the company has had. Kept
        as they come: which rows are one event is the reader's judgement, and the
        mirror does not make it (ADR 0016).
        """
        root = ticker[:_ROOT_LENGTH]
        body = await self._supplement(root)
        if body is None:
            await self._quarantine(
                root,
                "coverage-established",
                "B3 supplement response is absent or not a JSON object",
            )
        rows = body.get("stockDividends")
        if not isinstance(rows, list):
            await self._quarantine(
                root,
                "response-schema",
                "B3 supplement lacks a stockDividends list",
                evidence=body,
            )
        if not rows:
            # A company with no corporate action in its history is the normal
            # case, and it is an absence the mirror records rather than an empty
            # list it invents.
            await self._record(self._validation(root, rows=0))
            raise SourceNotFoundError(f"B3 lists no corporate action for {ticker}")
        if not all(isinstance(row, dict) for row in rows):
            await self._quarantine(
                root,
                "response-schema",
                "B3 stockDividends contains a non-object row",
                evidence=body,
            )
        required = {"isinCode", "approvedOn", "label"}
        missing = next(
            (
                sorted(required - set(row))
                for row in rows
                if isinstance(row, dict) and required - set(row)
            ),
            None,
        )
        if missing is not None:
            await self._quarantine(
                root,
                "response-schema",
                f"B3 stockDividends row lacks {', '.join(missing)}",
                evidence=body,
            )
        await self._record(self._validation(root, rows=len(rows)))

        code = self._code_of(body, ticker)
        return [
            RawFetchResult(
                module=module,
                request={
                    "source": "b3",
                    "endpoint": "GetListedSupplementCompany",
                    "issuing_company": root,
                    "statement": module,
                    # What tells one filed row from another: the same event is
                    # listed once per ISIN, and one approval date can carry two
                    # events (VIVT3's split and grupamento, 2025-03-13).
                    "isin_code": _text(row.get("isinCode")),
                    "approval_date": _text(row.get("approvedOn")),
                    "event_type": _text(row.get("label")),
                },
                http_status=200,
                payload=_to_payload(row, root, code),
                cvm_code=code,
            )
            for row in rows
            if isinstance(row, dict)
        ]

    def _validation(
        self,
        root: str,
        *,
        rows: int,
        findings: tuple[ValidationFinding, ...] = (),
        evidence: Mapping[str, object] | None = None,
    ) -> SourceBatchValidation:
        return SourceBatchValidation(
            source="b3",
            batch=f"GetListedSupplementCompany:{root}",
            module=CAPITAL_EVENT_B3_MODULE,
            parser=self.parser_identity,
            rules=_RULES,
            observations={"rows": rows, "coverage_established": not findings},
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

    async def _supplement(self, root: str) -> Mapping[str, Any] | None:
        payload = _encoded({"issuingCompany": root, "language": "pt-br"})
        url = f"{self._base_url}/GetListedSupplementCompany/{payload}"
        try:
            response = await self._http.get(
                url, headers={"User-Agent": _USER_AGENT}, timeout=30.0
            )
        except httpx.HTTPError as exc:
            logger.warning("B3 supplement failed for %s: %s", root, exc)
            return None
        if response.status_code != httpx.codes.OK or not response.text.strip():
            # An empty body is how the endpoint says "no such listed company" —
            # the normal answer for a delisted filer, and not an error.
            return None
        try:
            body = response.json()
        except ValueError:
            logger.warning("B3 supplement for %s was not JSON", root)
            return None
        if isinstance(body, list):
            body = body[0] if body else None
        return body if isinstance(body, dict) else None

    def _code_of(self, body: Mapping[str, Any], ticker: str) -> str | None:
        """The registrant the mirror is keyed on (ADR 0030).

        B3 answers with its own ``codeCVM``, which is the same registrant CVM
        names — but the composition root's map is the authority, because it is
        what every other module was stored under.
        """
        curated = self._ticker_to_code.get(ticker)
        if curated is not None:
            return curated
        code = body.get("codeCVM")
        return str(code) if code is not None and str(code).strip() else None


def _to_payload(row: Mapping[str, Any], root: str, code: str | None) -> dict[str, Any]:
    """Mirror the row as B3 publishes it — its vocabulary, its number format."""
    return {
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


def _text(value: Any) -> str:
    return str(value).strip() if value is not None else ""


def _encoded(params: dict[str, object]) -> str:
    """B3's proxy takes its parameters as base64-encoded JSON in the path."""
    return base64.b64encode(json.dumps(params).encode()).decode()
