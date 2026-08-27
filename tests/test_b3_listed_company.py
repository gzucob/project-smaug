"""B3 company resolution follows the registrant, never a guessed name."""

from __future__ import annotations

import base64
import json
from datetime import date

import httpx
import pytest

from smaug.ingestion.infrastructure.b3_listed_company import (
    B3CompanyResolutionError,
    B3ListedCompanyResolver,
)


def _params(request: httpx.Request) -> dict[str, object]:
    encoded = request.url.path.rsplit("/", 1)[-1]
    body = json.loads(base64.b64decode(encoded).decode())
    assert isinstance(body, dict)
    return body


class _Transport(httpx.AsyncBaseTransport):
    def __init__(self, *, initial_code: str | None, current_code: str = "2437"):
        self._initial_code = initial_code
        self._current_code = current_code
        self.calls: list[dict[str, object]] = []

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        params = _params(request)
        self.calls.append(params)
        if "GetDetail" in request.url.path:
            return httpx.Response(
                200,
                json={
                    "issuingCompany": "AXIA",
                    "tradingName": "AXIA ENERGIA",
                    "codeCVM": "2437",
                    "dateQuotation": "26/04/2012",
                },
            )
        root = params["issuingCompany"]
        if root == "ELET" and self._initial_code is None:
            return httpx.Response(200, text="")
        code = self._initial_code if root == "ELET" else self._current_code
        return httpx.Response(
            200,
            json={
                "code": root,
                "codeCVM": code,
                "tradingName": "OTHER" if root == "ELET" else "AXIA ENERGIA",
            },
        )


async def test_a_reused_root_is_not_accepted_for_another_registrant() -> None:
    transport = _Transport(initial_code="9999")
    async with httpx.AsyncClient(transport=transport) as http:
        company = await B3ListedCompanyResolver(
            http, base_url="https://b3.test"
        ).resolve("ELET3", cvm_code="2437")

    assert company.issuing_company == "AXIA"
    assert company.trading_name == "AXIA ENERGIA"
    assert company.cvm_code == "2437"
    assert transport.calls == [
        {"issuingCompany": "ELET", "language": "pt-br"},
        {"codeCVM": "2437", "language": "pt-br"},
        {"issuingCompany": "AXIA", "language": "pt-br"},
    ]


async def test_the_current_supplement_must_confirm_the_same_registrant() -> None:
    transport = _Transport(initial_code=None, current_code="9999")
    async with httpx.AsyncClient(transport=transport) as http:
        with pytest.raises(B3CompanyResolutionError) as captured:
            await B3ListedCompanyResolver(http, base_url="https://b3.test").resolve(
                "ELET3", cvm_code="2437"
            )

    assert captured.value.code == "response-schema"
    assert "different CVM registrant" in captured.value.detail


async def test_an_absent_root_without_a_cvm_code_stays_unestablished() -> None:
    transport = _Transport(initial_code=None)
    async with httpx.AsyncClient(transport=transport) as http:
        with pytest.raises(B3CompanyResolutionError) as captured:
            await B3ListedCompanyResolver(http, base_url="https://b3.test").resolve(
                "ELET3", cvm_code=None
            )

    assert captured.value.code == "coverage-established"
    assert transport.calls == [{"issuingCompany": "ELET", "language": "pt-br"}]


async def test_an_empty_detail_object_is_an_absent_company_not_a_schema_match() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return (
            httpx.Response(200, json={})
            if "GetDetail" in request.url.path
            else httpx.Response(200, text="")
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        with pytest.raises(B3CompanyResolutionError) as captured:
            await B3ListedCompanyResolver(http, base_url="https://b3.test").resolve(
                "ELMD3", cvm_code="25569"
            )

    assert captured.value.code == "coverage-established"
    assert "names no listed company" in captured.value.detail


async def test_detail_quotation_date_is_preserved_for_historical_recovery() -> None:
    transport = _Transport(initial_code=None)
    async with httpx.AsyncClient(transport=transport) as http:
        company = await B3ListedCompanyResolver(
            http, base_url="https://b3.test"
        ).resolve_by_cvm("2437")

    assert company.quotation_date == date(2012, 4, 26)
