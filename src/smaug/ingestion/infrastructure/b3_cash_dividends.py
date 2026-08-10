"""Cash payouts as **B3** publishes them — the exchange's dividend record.

The dividend-adjusted price basis has no published series anywhere: B3's quote
file carries the price as traded and nothing else (ADR 0032), and the vendor
that used to supply it is being removed. It is rebuilt from this, which is the
one record complete enough to rebuild it from — 900 rows for Bradesco reaching
back to 1995-12-28, where the same company's *stock* events number one.

Two endpoints are needed and only together:

``GetListedSupplementCompany`` answers on a trading root (``BBDC``) and returns
the ``tradingName`` (``BRADESCO``); ``GetListedCashDividends`` takes only that
name, and is paginated. The supplement carries a ``cashDividends`` list of its
own, and it is *not* the same thing: 32 recent rows against the paginated
endpoint's 900.

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

The retry is a **respelling, not a search**. Truncating a name until something
answers looks like it works and does not: ``KLABIN`` answers with 18 rows from
1996-2001 and ``KLABIN S.A.`` with 219, because the short name is a different,
dead registrant. The response carries no company identity to catch that with —
its fields are share class, date, value and reference price — so a wrong name
returns a perfectly valid history of somebody else. Only the two spellings of
one name are tried, and nothing else (#190).
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

# The exchange's cash payouts, keyed on the trading root.
CASH_DIVIDEND_B3_MODULE = "CASH_DIVIDEND_B3"

_USER_AGENT = "Mozilla/5.0"
_ROOT_LENGTH = 4

# The endpoint returns an empty result set above a few hundred, so the history is
# walked rather than asked for whole.
_PAGE_SIZE = 100

# A company paying monthly since the nineties reaches ~900 rows; the bound only
# exists so a malformed ``totalPages`` cannot spin.
_MAX_PAGES = 60
_RULES = (
    ValidationRule("response-schema", 1),
    ValidationRule("coverage-established", 1),
    ValidationRule("record-count", 1),
)


class B3CashDividendSource:
    """Fetch every cash payout B3 lists for one ticker's company."""

    parser_identity = ParserIdentity("b3.cash-dividends.json", 1)

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
        """Every cash-dividend row B3 lists, one result per row.

        One row per event **per share class** — B3 files a payment once for ON
        and once for PN, at rates that differ. Which rows a ticker reads is the
        analysis context's judgement, not the mirror's.
        """
        root = ticker[:_ROOT_LENGTH]
        supplement = await self._supplement(root)
        if supplement is None:
            await self._quarantine(
                root,
                "coverage-established",
                "B3 supplement response is absent or not a JSON object",
            )
        trading_name = _text(supplement.get("tradingName"))
        if not trading_name:
            await self._quarantine(
                root,
                "response-schema",
                "B3 supplement lacks a tradingName",
                evidence=supplement,
            )
        rows = await self._dividends(root, trading_name)
        respelled = trading_name.replace("/", ".")
        if not rows and respelled != trading_name:
            logger.info(
                "B3 gave no payout for %r; the dividend table spells it %r",
                trading_name,
                respelled,
            )
            rows = await self._dividends(root, respelled)
        if not rows:
            # A company that has never paid is the normal case for a recent
            # listing, and it is an absence the mirror records rather than an
            # empty list it invents.
            await self._record(self._validation(root, rows=0))
            raise SourceNotFoundError(f"B3 lists no cash payout for {ticker}")

        if not all(isinstance(row, Mapping) for row in rows):
            await self._quarantine(
                root,
                "response-schema",
                "B3 dividends contains a non-object row",
            )
        required = {"typeStock", "lastDatePriorEx", "corporateAction", "valueCash"}
        missing = next(
            (sorted(required - set(row)) for row in rows if required - set(row)),
            None,
        )
        if missing is not None:
            await self._quarantine(
                root,
                "response-schema",
                f"B3 dividends row lacks {', '.join(missing)}",
            )
        await self._record(self._validation(root, rows=len(rows)))

        code = self._code_of(supplement, ticker)
        return [
            RawFetchResult(
                module=module,
                request={
                    "source": "b3",
                    "endpoint": "GetListedCashDividends",
                    "issuing_company": root,
                    "trading_name": trading_name,
                    "statement": module,
                    # What tells one filed row from another: the same payment is
                    # listed once per class, and one ex date can carry both a
                    # dividend and interest on own capital.
                    "share_class": _text(row.get("typeStock")),
                    "last_date_prior": _text(row.get("lastDatePriorEx")),
                    "event_type": _text(row.get("corporateAction")),
                    "value": _text(row.get("valueCash")),
                },
                http_status=200,
                payload=_to_payload(row, root, code),
                cvm_code=code,
            )
            for row in rows
        ]

    async def _supplement(self, root: str) -> Mapping[str, Any] | None:
        body = await self._json(
            f"{self._base_url}/GetListedSupplementCompany/"
            + _encoded({"issuingCompany": root, "language": "pt-br"}),
            root,
        )
        if isinstance(body, list):
            body = body[0] if body else None
        return body if isinstance(body, dict) else None

    async def _dividends(self, root: str, trading_name: str) -> list[Mapping[str, Any]]:
        rows: list[Mapping[str, Any]] = []
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
            if not all(isinstance(row, Mapping) for row in results):
                await self._quarantine(
                    root,
                    "response-schema",
                    "B3 dividends results contains a non-object row",
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
        findings: tuple[ValidationFinding, ...] = (),
        evidence: Mapping[str, object] | None = None,
    ) -> SourceBatchValidation:
        return SourceBatchValidation(
            source="b3",
            batch=f"GetListedCashDividends:{root}",
            module=CASH_DIVIDEND_B3_MODULE,
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

    def _code_of(self, body: Mapping[str, Any], ticker: str) -> str | None:
        """The registrant the mirror is keyed on (ADR 0030)."""
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
    }


def _text(value: Any) -> str:
    return str(value).strip() if value is not None else ""


def _encoded(params: dict[str, object]) -> str:
    """B3's proxy takes its parameters as base64-encoded JSON in the path."""
    return base64.b64encode(json.dumps(params).encode()).decode()
