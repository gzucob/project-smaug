"""MCP contracts preserve API data and expose only bounded read operations."""

from __future__ import annotations

import json
import sys
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

import httpx
import pytest
from mcp import Client, StdioServerParameters
from mcp.types import TextContent, TextResourceContents
from pydantic import JsonValue

from smaug.entrypoints.mcp import create_server
from smaug.shared.config import Settings
from smaug.shared.mcp_api import McpApiError, McpApiReader, normalize_ticker, paginate


async def test_tools_discovery_mapping_and_preservation() -> None:
    requests: list[httpx.Request] = []
    analysis = {
        "price": "20.1234567890123456789",
        "computed_at": "2026-10-13T12:00:00Z",
        "indicators": {"roe": None},
        "null_reasons": {"roe": "missing_equity"},
        "indicator_contract": {"roe": {"basis": "filed"}},
        "capital_provenance": {"source": "CVM"},
    }
    responses: dict[str, JsonValue] = {
        "/portfolio": [{"ticker": "BBAS3"}, {"ticker": "PETR4"}],
        "/analysis/summary": [{"ticker": "BBAS3", "price": None}],
        "/analysis/BBAS3": {
            "ticker": "BBAS3",
            "ttm": analysis,
            "history": [{"view": "2024"}, {"view": "2025"}],
        },
        "/prices/BBAS3/history": {
            "ticker": "BBAS3",
            "source": "B3",
            "price_basis": "split_adjusted",
            "gaps": [{"year": 2010, "reason": "missing_price"}],
            "points": [{"as_traded": "40", "adjusted": "20", "factor": "2"}],
        },
        "/dividends/BBAS3/history": {
            "ticker": "BBAS3",
            "coverage": "unresolved",
            "reason": "unresolved_share_class",
            "events": [],
        },
        "/shareholders/BBAS3/position": {
            "ticker": "BBAS3",
            "reference_date": "2025-12-31",
            "version": 2,
            "coverage": "available",
            "reason": None,
            "positions": [{"name": "Filed name", "total_percent": "50.01"}],
        },
    }

    def handle(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200, json=responses[request.url.path])

    server = create_server(Settings(), transport=httpx.MockTransport(handle))
    async with Client(server) as client:
        discovered = await client.list_tools()
        assert {tool.name for tool in discovered.tools} == {
            "get_setup_guide",
            "get_service_status",
            "list_portfolio",
            "list_analysis_summaries",
            "get_ticker_analysis",
            "get_price_history",
            "get_dividend_history",
            "get_shareholding_position",
        }
        for tool in discovered.tools:
            assert tool.annotations is not None
            assert tool.annotations.read_only_hint is True
            assert tool.output_schema is not None
        for tool in discovered.tools:
            if tool.name in {"get_setup_guide", "get_service_status"}:
                continue
            arguments: dict[str, JsonValue] = {"limit": 1}
            if tool.name.startswith("get_"):
                arguments["ticker"] = " bbas3 "
            result = await client.call_tool(tool.name, arguments)
            assert not result.is_error
            assert result.structured_content is not None
            assert isinstance(result.content[0], TextContent)
            assert json.loads(result.content[0].text) == result.structured_content
            if tool.name == "get_ticker_analysis":
                assert result.structured_content["ttm"] == analysis
                assert result.structured_content["history"] == [{"view": "2024"}]
                assert result.structured_content["pagination"]["next_offset"] == 1
            if tool.name == "get_price_history":
                assert (
                    result.structured_content["gaps"]
                    == responses["/prices/BBAS3/history"]["gaps"]
                )
            if tool.name == "get_dividend_history":
                assert result.structured_content["coverage"] == "unresolved"
                assert result.structured_content["reason"] == "unresolved_share_class"
            if tool.name == "get_shareholding_position":
                assert result.structured_content["reference_date"] == "2025-12-31"
        page = await client.call_tool("list_portfolio", {"offset": 1, "limit": 1})
        assert page.structured_content == {
            "items": [{"ticker": "PETR4"}],
            "total": 2,
            "offset": 1,
            "limit": 1,
            "next_offset": None,
        }
        for arguments in ({"limit": 501}, {"limit": 0}, {"offset": -1}):
            invalid = await client.call_tool("list_portfolio", arguments)
            assert invalid.is_error
        for ticker in ("", "../BBAS3", "BBAS3?x=1", "BBAS3\\x", "%2f", "BB AS3"):
            invalid = await client.call_tool("get_ticker_analysis", {"ticker": ticker})
            assert invalid.is_error
    assert len(requests) == 7
    assert all(request.method == "GET" for request in requests)


@pytest.mark.parametrize("status", [404, 422, 500, 302])
async def test_http_failures_preserve_status_and_named_reason(status: int) -> None:
    def handle(request: httpx.Request) -> httpx.Response:
        return httpx.Response(status, json={"detail": "price_history_not_prepared"})

    server = create_server(Settings(), transport=httpx.MockTransport(handle))
    async with Client(server) as client:
        result = await client.call_tool("get_price_history", {"ticker": "BBAS3"})
        assert result.is_error
        assert isinstance(result.content[0], TextContent)
        assert str(status) in result.content[0].text
        assert "price_history_not_prepared" in result.content[0].text


@pytest.mark.parametrize(
    ("failure", "reason"),
    [(httpx.ConnectError, "api_unavailable"), (httpx.ReadTimeout, "api_timeout")],
)
async def test_network_failures_are_tool_errors(
    failure: type[httpx.RequestError], reason: str
) -> None:
    def handle(request: httpx.Request) -> httpx.Response:
        raise failure("simulated", request=request)

    server = create_server(Settings(), transport=httpx.MockTransport(handle))
    async with Client(server) as client:
        assert len((await client.list_tools()).tools) == 8
        result = await client.call_tool("list_portfolio")
        assert result.is_error
        assert isinstance(result.content[0], TextContent)
        assert reason in result.content[0].text


@pytest.mark.parametrize("body", ["not json", "{}", "[null]", "[NaN]"])
async def test_invalid_api_responses_are_tool_errors(body: str) -> None:
    transport = httpx.MockTransport(lambda request: httpx.Response(200, text=body))
    async with Client(create_server(Settings(), transport=transport)) as client:
        result = await client.call_tool("list_portfolio")
        assert result.is_error
        assert isinstance(result.content[0], TextContent)
        assert "invalid_api_response" in result.content[0].text


async def test_numeric_decimal_tokens_do_not_round() -> None:
    transport = httpx.MockTransport(
        lambda request: httpx.Response(
            200, text='[{"price": 20.123456789012345678901, "missing": null}]'
        )
    )
    async with Client(create_server(Settings(), transport=transport)) as client:
        result = await client.call_tool("list_analysis_summaries")
        assert result.structured_content["items"] == [
            {"price": "20.123456789012345678901", "missing": None}
        ]


@pytest.mark.parametrize("coverage", ["empty", "unavailable", "unresolved"])
async def test_empty_dividend_pages_preserve_coverage(coverage: str) -> None:
    payload = {
        "ticker": "BBAS3",
        "coverage": coverage,
        "reason": None if coverage == "empty" else "named_missing_reason",
        "events": [],
    }
    transport = httpx.MockTransport(lambda request: httpx.Response(200, json=payload))
    async with Client(create_server(Settings(), transport=transport)) as client:
        result = await client.call_tool("get_dividend_history", {"ticker": "BBAS3"})
        assert not result.is_error
        assert result.structured_content["coverage"] == coverage
        assert result.structured_content["reason"] == payload["reason"]
        assert result.structured_content["pagination"]["total"] == 0


async def test_closed_year_history_does_not_require_ttm() -> None:
    transport = httpx.MockTransport(
        lambda request: httpx.Response(
            200, json={"ticker": "BBAS3", "ttm": None, "history": [{"view": "2025"}]}
        )
    )
    async with Client(create_server(Settings(), transport=transport)) as client:
        result = await client.call_tool("get_ticker_analysis", {"ticker": "BBAS3"})
        assert not result.is_error
        assert result.structured_content["ttm"] is None
        assert result.structured_content["history"] == [{"view": "2025"}]


@pytest.mark.parametrize(
    "payload",
    [
        {"ticker": "PETR4", "ttm": None, "history": []},
        {"ticker": "BBAS3", "ttm": None, "history": None},
        {"ticker": "BBAS3", "ttm": None, "history": [None]},
        {"ticker": "BBAS3", "ttm": "invalid", "history": []},
        {"ticker": "BBAS3", "history": []},
    ],
)
async def test_invalid_ticker_response_shapes_are_tool_errors(
    payload: JsonValue,
) -> None:
    transport = httpx.MockTransport(lambda request: httpx.Response(200, json=payload))
    async with Client(create_server(Settings(), transport=transport)) as client:
        result = await client.call_tool("get_ticker_analysis", {"ticker": "BBAS3"})
        assert result.is_error
        assert isinstance(result.content[0], TextContent)
        assert "invalid_api_response" in result.content[0].text


async def test_http_configuration_and_client_cleanup() -> None:
    requests: list[httpx.Request] = []

    class Transport(httpx.MockTransport):
        closed = False

        async def aclose(self) -> None:
            self.closed = True

    def handle(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200, json=[])

    transport = Transport(handle)
    settings = Settings(
        mcp_api_base_url="http://localhost:8765/prefix/", mcp_api_timeout_seconds=7
    )
    async with Client(create_server(settings, transport=transport)) as client:
        assert not (await client.call_tool("list_portfolio")).is_error
        assert not (await client.call_tool("list_analysis_summaries")).is_error
        assert not transport.closed
    assert transport.closed
    assert [str(request.url) for request in requests] == [
        "http://localhost:8765/prefix/portfolio",
        "http://localhost:8765/prefix/analysis/summary",
    ]
    assert requests[0].extensions["timeout"]["read"] == 7


def test_pagination_handles_empty_and_exhausted_lists() -> None:
    assert paginate([], 0, 100)["next_offset"] is None
    assert paginate([1, 2], 5, 1)["items"] == []
    assert paginate([1, 2], 5, 1)["total"] == 2
    assert normalize_ticker(" bbas3 ") == "BBAS3"


async def test_adapter_rejects_unregistered_endpoints() -> None:
    async with httpx.AsyncClient() as client:
        reader = McpApiReader(client)
        with pytest.raises(McpApiError, match="unsupported_api_path"):
            await reader.read_list("prices/refresh-status", 0, 100)
        with pytest.raises(McpApiError, match="unsupported_api_path"):
            await reader.read_ticker("write", "BBAS3", "history", 0, 100)


async def test_stdio_subprocess_discovers_tools_and_reads_http() -> None:
    requests: list[str] = []

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:  # noqa: N802 -- HTTPServer handler convention.
            requests.append(self.path)
            payload = {
                "/portfolio": [{"ticker": "BBAS3"}],
                "/health": {"status": "ok", "commit": "stdio-test"},
                "/ready": {
                    "ready": True,
                    "mongo": "ok",
                    "postgres": "ok",
                    "schema": "ok",
                    "data": "empty",
                },
            }
            body = json.dumps(payload[self.path]).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, format: str, *args: object) -> None:
            pass

    http_server = HTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=http_server.serve_forever, daemon=True)
    thread.start()
    try:
        parameters = StdioServerParameters(
            command=sys.executable,
            args=["-m", "smaug.entrypoints.mcp"],
            cwd=Path(__file__).resolve().parents[1],
            env={"MCP_API_BASE_URL": f"http://127.0.0.1:{http_server.server_port}"},
        )
        async with Client(parameters, read_timeout_seconds=15) as client:
            assert len((await client.list_tools()).tools) == 8
            result = await client.call_tool("list_portfolio")
            assert not result.is_error
            assert result.structured_content["items"] == [{"ticker": "BBAS3"}]
            guide = await client.call_tool(
                "get_setup_guide", {"platform": "linux", "edition": "backend"}
            )
            assert not guide.is_error
            resource = await client.read_resource("smaug://setup/linux/backend")
            assert isinstance(resource.contents[0], TextResourceContents)
            assert resource.contents[0].text == guide.structured_content["markdown"]
            status = await client.call_tool("get_service_status")
            assert not status.is_error
            assert status.structured_content["status"] == "ready_empty"
        assert requests == ["/portfolio", "/health", "/ready"]
    finally:
        http_server.shutdown()
        thread.join(timeout=5)
        http_server.server_close()
