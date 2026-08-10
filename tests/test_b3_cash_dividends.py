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

from smaug.ingestion.infrastructure.b3_cash_dividends import (
    CASH_DIVIDEND_B3_MODULE,
    B3CashDividendSource,
)
from smaug.shared.errors import SourceBatchValidationError, SourceNotFoundError

SUPPLEMENT = {"tradingName": "BRADESCO", "codeCVM": "906"}
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

    def __init__(self, pages: int = 1) -> None:
        self.pages = pages
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
                "results": ROWS if page <= self.pages else [],
            },
        )


async def test_every_row_is_mirrored_as_b3_filed_it() -> None:
    transport = _Transport()
    async with httpx.AsyncClient(transport=transport) as http:
        results = await B3CashDividendSource(http).fetch(
            "BBDC4", CASH_DIVIDEND_B3_MODULE
        )

    assert len(results) == 2
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

    assert len(results) == 6  # two rows on each of three pages
    assert [call.get("pageNumber") for call in transport.asked[1:]] == [1, 2, 3]


async def test_a_company_that_has_never_paid_is_an_absence_not_an_empty_list() -> None:
    class _Empty(_Transport):
        async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
            if "GetListedSupplementCompany" in str(request.url):
                return httpx.Response(200, json=SUPPLEMENT)
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
