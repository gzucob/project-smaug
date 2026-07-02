"""brapi client: request shaping and status-code handling (no network)."""

import httpx
import pytest

from smaug.ingestion.infrastructure.brapi_client import BrapiClient
from smaug.shared.errors import (
    BrapiAuthError,
    BrapiNotFoundError,
    BrapiRateLimitError,
    BrapiUnexpectedStatusError,
)


def _mock_client(handler: object) -> httpx.AsyncClient:
    transport = httpx.MockTransport(handler)  # type: ignore[arg-type]
    return httpx.AsyncClient(transport=transport)


async def test_should_return_payload_and_hide_token_when_status_ok() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/api/quote/PETR4"
        assert request.url.params["modules"] == "incomeStatementHistoryQuarterly"
        assert request.url.params["token"] == "SECRET"
        return httpx.Response(200, json={"results": [{"symbol": "PETR4"}]})

    async with _mock_client(handler) as http:
        client = BrapiClient("https://brapi.dev/api", "SECRET", http)
        result = await client.fetch("PETR4", "incomeStatementHistoryQuarterly")

    assert result.http_status == 200
    assert result.payload["results"][0]["symbol"] == "PETR4"
    # The secret must never reach the persisted audit metadata.
    assert "token" not in result.request["params"]


async def test_should_use_dividends_flag_when_module_is_dividends() -> None:
    captured: dict[str, str] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured.update(dict(request.url.params))
        return httpx.Response(200, json={})

    async with _mock_client(handler) as http:
        client = BrapiClient("https://brapi.dev/api", "SECRET", http)
        await client.fetch("PETR4", "dividends")

    assert captured["dividends"] == "true"
    assert "modules" not in captured


@pytest.mark.parametrize(
    ("status", "expected"),
    [
        (401, BrapiAuthError),
        (402, BrapiRateLimitError),
        (429, BrapiRateLimitError),
        (404, BrapiNotFoundError),
        (500, BrapiUnexpectedStatusError),
    ],
)
async def test_should_map_status_code_to_typed_error(
    status: int, expected: type[Exception]
) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(status, text="boom")

    async with _mock_client(handler) as http:
        client = BrapiClient("https://brapi.dev/api", "SECRET", http)
        with pytest.raises(expected):
            await client.fetch("PETR4", "financialData")
