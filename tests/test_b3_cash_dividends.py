"""B3's cash-payout record, mirrored as filed.

Two endpoints answer together: the supplement turns a trading root into the
``tradingName`` the paginated one needs, and only the paginated one carries the
history — 900 rows for Bradesco against the supplement's 32.
"""

from __future__ import annotations

import base64
import json

import httpx
import pytest

from smaug.ingestion.domain.validation import SourceBatchValidation
from smaug.ingestion.infrastructure.b3_cash_dividends import (
    CASH_DIVIDEND_B3_MODULE,
    B3CashDividendSource,
)
from smaug.shared.errors import SourceBatchValidationError, SourceNotFoundError

SUPPLEMENT = {"code": "BBDC", "tradingName": "BRADESCO", "codeCVM": "906"}
ROWS = [
    {
        "typeStock": "ON",
        "dateApproval": "23/06/2026",
        "valueCash": "0,315359035",
        "corporateAction": "JRS CAP PROPRIO",
        "lastDatePriorEx": "03/07/2026",
        "closingPricePriorExDate": "15,88",
        "quotedPerShares": "1",
        "corporateActionPrice": "1,985888",
    },
    {
        "typeStock": "PN",
        "dateApproval": None,
        "valueCash": "0,006",
        "corporateAction": "DIVIDENDO",
        "lastDatePriorEx": "28/12/1995",
        "closingPricePriorExDate": "8,50",
        "quotedPerShares": "1000",
        "corporateActionPrice": "0,070588",
    },
]


def _decoded(url: str) -> dict[str, object]:
    payload = url.rsplit("/", 1)[-1]
    decoded = json.loads(base64.b64decode(payload).decode())
    assert isinstance(decoded, dict)
    return decoded


class _Transport(httpx.AsyncBaseTransport):
    """Serves the supplement and one page of dividends, recording the calls."""

    def __init__(
        self, pages: int = 1, rows: list[dict[str, object]] | None = None
    ) -> None:
        self.pages = pages
        self.rows = ROWS if rows is None else rows
        self.asked: list[dict[str, object]] = []

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        params = _decoded(url)
        self.asked.append(params)
        if "GetListedSupplementCompany" in url:
            return httpx.Response(200, json=SUPPLEMENT)
        page = int(str(params["pageNumber"]))
        return httpx.Response(
            200,
            json={
                "page": {"pageNumber": page, "totalPages": self.pages},
                "results": self.rows if page <= self.pages else [],
            },
        )


async def test_every_row_is_mirrored_as_b3_filed_it() -> None:
    transport = _Transport()
    async with httpx.AsyncClient(transport=transport) as http:
        results = await B3CashDividendSource(http).fetch(
            "BBDC4", CASH_DIVIDEND_B3_MODULE
        )

    assert len(results) == 2
    assert {result.source for result in results} == {"b3"}
    first = results[0].payload
    # pt-BR as it came, and the scale left in place: ``quotedPerShares`` says
    # whether the payment is per share or per lot of a thousand.
    assert first["value"] == "0,315359035"
    assert first["quoted_per_shares"] == "1"
    assert first["percentage_of_price"] == "1,985888"
    assert first["last_date_prior"] == "03/07/2026"
    assert results[1].payload["quoted_per_shares"] == "1000"


async def test_the_class_and_the_ex_date_identify_a_row() -> None:
    # B3 files one payment per class at rates that differ, and one ex date can
    # carry both a dividend and interest on own capital.
    transport = _Transport()
    async with httpx.AsyncClient(transport=transport) as http:
        results = await B3CashDividendSource(http).fetch(
            "BBDC4", CASH_DIVIDEND_B3_MODULE
        )

    assert results[0].request["share_class"] == "ON"
    assert results[1].request["share_class"] == "PN"
    assert results[0].request["last_date_prior"] == "03/07/2026"


async def test_the_trading_name_comes_from_the_supplement() -> None:
    """The paginated endpoint takes only a trading name, and a root is not one.

    ``BBDC`` returns nothing; ``BRADESCO`` returns the history.
    """
    transport = _Transport()
    async with httpx.AsyncClient(transport=transport) as http:
        await B3CashDividendSource(http).fetch("BBDC4", CASH_DIVIDEND_B3_MODULE)

    assert transport.asked[0] == {"issuingCompany": "BBDC", "language": "pt-br"}
    assert transport.asked[1]["tradingName"] == "BRADESCO"


async def test_the_history_is_walked_page_by_page() -> None:
    transport = _Transport(pages=3)
    async with httpx.AsyncClient(transport=transport) as http:
        results = await B3CashDividendSource(http).fetch(
            "BBDC4", CASH_DIVIDEND_B3_MODULE
        )

    assert len(results) == 2  # two economic rows repeated on each page
    assert [call.get("pageNumber") for call in transport.asked[1:]] == [1, 2, 3]


async def test_a_company_that_has_never_paid_is_an_absence_not_an_empty_list() -> None:
    class _Empty(_Transport):
        async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
            if "GetListedSupplementCompany" in str(request.url):
                return httpx.Response(
                    200,
                    json={"code": "RDNI", "tradingName": "RNI", "codeCVM": "20451"},
                )
            return httpx.Response(
                200, json={"page": {"pageNumber": 1, "totalPages": 0}, "results": []}
            )

    async with httpx.AsyncClient(transport=_Empty()) as http:
        with pytest.raises(SourceNotFoundError):
            await B3CashDividendSource(http).fetch("RDNI3", CASH_DIVIDEND_B3_MODULE)


async def test_malformed_pagination_quarantines_coverage() -> None:
    class _Malformed(_Transport):
        async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
            if "GetListedSupplementCompany" in str(request.url):
                return httpx.Response(200, json=SUPPLEMENT)
            return httpx.Response(
                200,
                json={"page": {"totalPages": 1}, "results": []},
            )

    async with httpx.AsyncClient(transport=_Malformed()) as http:
        with pytest.raises(SourceBatchValidationError, match="response-schema"):
            await B3CashDividendSource(http).fetch("RDNI3", CASH_DIVIDEND_B3_MODULE)


async def test_the_corporate_form_is_respelled_with_dots_and_retried() -> None:
    """B3 writes the same company two ways and the dividend table wants the dots.

    The supplement, ``GetInitialCompanies``, ``GetDetail`` and COTAHIST's short
    name all say ``AMBEV S/A``; the dividend table says ``AMBEV S.A.`` and
    returns nothing at all for the slash. It ignores a trailing dot, so swapping
    the slash for one is enough. Six companies of 371 are this, Ambev and
    Klabin among them.

    It is a respelling and not a search: ``KLABIN`` alone answers with 18 rows
    of a dead registrant where ``KLABIN S.A.`` answers with 219, and nothing in
    the response would tell the two apart.
    """

    class _Dotted(_Transport):
        async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
            url = str(request.url)
            params = _decoded(url)
            self.asked.append(params)
            if "GetListedSupplementCompany" in url:
                return httpx.Response(200, json={"tradingName": "AMBEV S/A   "})
            if params["tradingName"] != "AMBEV S.A":
                return httpx.Response(
                    200,
                    json={"page": {"pageNumber": 1, "totalPages": 0}, "results": []},
                )
            return httpx.Response(
                200, json={"page": {"pageNumber": 1, "totalPages": 1}, "results": ROWS}
            )

    transport = _Dotted()
    async with httpx.AsyncClient(transport=transport) as http:
        results = await B3CashDividendSource(http).fetch(
            "ABEV3", CASH_DIVIDEND_B3_MODULE
        )

    assert len(results) == 2
    assert [call.get("tradingName") for call in transport.asked[1:]] == [
        "AMBEV S/A",
        "AMBEV S.A",
    ]
    assert results[0].request["trading_name"] == "AMBEV S.A"


class _RenamedTransport(_Transport):
    """Resolve former ELET through CD_CVM to current AXIA before reading cash."""

    def __init__(self, rows: list[dict[str, object]]) -> None:
        super().__init__()
        self._rows = rows

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        params = _decoded(url)
        self.asked.append(params)
        if "GetDetail" in url:
            return httpx.Response(
                200,
                json={
                    "issuingCompany": "AXIA",
                    "tradingName": "AXIA ENERGIA",
                    "codeCVM": "2437",
                },
            )
        if "GetListedSupplementCompany" in url:
            if params["issuingCompany"] == "ELET":
                return httpx.Response(200, text="")
            return httpx.Response(
                200,
                json={
                    "code": "AXIA",
                    "codeCVM": "2437",
                    "tradingName": "AXIA ENERGIA",
                },
            )
        return httpx.Response(
            200,
            json={
                "page": {
                    "pageNumber": 1,
                    "totalPages": 1 if self._rows else 0,
                },
                "results": self._rows,
            },
        )


async def test_a_former_root_reads_cash_through_the_stable_cvm_registrant() -> None:
    transport = _RenamedTransport(ROWS)
    async with httpx.AsyncClient(transport=transport) as http:
        results = await B3CashDividendSource(
            http, ticker_to_code={"ELET3": "2437"}
        ).fetch("ELET3", CASH_DIVIDEND_B3_MODULE)

    assert len(results) == 2
    assert [call.get("issuingCompany") for call in transport.asked[:3]] == [
        "ELET",
        None,
        "AXIA",
    ]
    assert transport.asked[1]["codeCVM"] == "2437"
    assert transport.asked[3]["tradingName"] == "AXIA ENERGIA"
    assert results[0].request["issuing_company"] == "AXIA"
    assert results[0].request["trading_name"] == "AXIA ENERGIA"
    assert results[0].cvm_code == "2437"


async def test_a_registrant_fallback_can_prove_zero_cash_rows() -> None:
    class _Reporter:
        def __init__(self) -> None:
            self.reports: list[SourceBatchValidation] = []

        async def record(self, validation: SourceBatchValidation) -> None:
            self.reports.append(validation)

    reporter = _Reporter()
    transport = _RenamedTransport([])
    async with httpx.AsyncClient(transport=transport) as http:
        source = B3CashDividendSource(
            http,
            ticker_to_code={"ELET3": "2437"},
            validation_reporter=reporter,
        )
        with pytest.raises(SourceNotFoundError):
            await source.fetch("ELET3", CASH_DIVIDEND_B3_MODULE)

    assert reporter.reports[-1].status.value == "accepted"
    assert reporter.reports[-1].observations == {
        "rows": 0,
        "fetched": 0,
        "accepted": 0,
        "rejected": 0,
        "deduplicated": 0,
        "coverage_established": True,
    }


async def test_row_reconciliation_records_rejections_and_duplicate_identities() -> None:
    duplicate = dict(ROWS[0])
    same_day_other_right = {
        **ROWS[0],
        "corporateAction": "DIVIDENDO",
        "valueCash": "0,100000000",
    }
    malformed = dict(ROWS[0])
    del malformed["quotedPerShares"]
    source_rows = [ROWS[0], same_day_other_right, duplicate, malformed]

    class _Reporter:
        def __init__(self) -> None:
            self.reports: list[SourceBatchValidation] = []

        async def record(self, validation: SourceBatchValidation) -> None:
            self.reports.append(validation)

    reporter = _Reporter()
    transport = _Transport(rows=source_rows)
    async with httpx.AsyncClient(transport=transport) as http:
        results = await B3CashDividendSource(http, validation_reporter=reporter).fetch(
            "BBDC4", CASH_DIVIDEND_B3_MODULE
        )

    assert len(results) == 2
    report = reporter.reports[-1]
    assert report.status.value == "accepted"
    assert report.observations == {
        "rows": 4,
        "fetched": 4,
        "accepted": 2,
        "rejected": 1,
        "deduplicated": 1,
        "coverage_established": False,
    }
    rejected = report.evidence["rejected_rows"]
    assert isinstance(rejected, list)
    assert rejected[0]["finding"]["code"] == "response-schema"
    assert "quotedPerShares" in rejected[0]["finding"]["detail"]
    duplicates = report.evidence["deduplicated_rows"]
    assert isinstance(duplicates, list)
    assert duplicates[0]["matches"] == 1
    assert duplicates[0]["identity"] == {
        "share_class": "ON",
        "event_type": "JRS CAP PROPRIO",
        "last_date_prior": "03/07/2026",
        "value": "0.315359035",
        "quoted_per_shares": "1",
    }


async def test_rows_with_changed_published_metadata_are_not_source_duplicates() -> None:
    amended = {
        **ROWS[0],
        "dateApproval": "24/06/2026",
        "closingPricePriorExDate": "15,90",
        "corporateActionPrice": "1,983000",
    }
    transport = _Transport(rows=[ROWS[0], amended])
    async with httpx.AsyncClient(transport=transport) as http:
        results = await B3CashDividendSource(http).fetch(
            "BBDC4", CASH_DIVIDEND_B3_MODULE
        )

    assert len(results) == 2


async def test_all_rejected_rows_quarantine_with_a_nonempty_coverage_finding() -> None:
    malformed = dict(ROWS[0])
    del malformed["typeStock"]

    class _Reporter:
        def __init__(self) -> None:
            self.reports: list[SourceBatchValidation] = []

        async def record(self, validation: SourceBatchValidation) -> None:
            self.reports.append(validation)

    reporter = _Reporter()
    transport = _Transport(rows=[malformed])
    async with httpx.AsyncClient(transport=transport) as http:
        source = B3CashDividendSource(http, validation_reporter=reporter)
        with pytest.raises(SourceBatchValidationError, match="response-schema"):
            await source.fetch("BBDC4", CASH_DIVIDEND_B3_MODULE)

    report = reporter.reports[-1]
    assert report.status.value == "quarantined"
    assert report.observations == {
        "rows": 1,
        "fetched": 1,
        "accepted": 0,
        "rejected": 1,
        "deduplicated": 0,
        "coverage_established": False,
    }
