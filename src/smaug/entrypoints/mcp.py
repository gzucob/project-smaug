"""Local stdio MCP tools over the existing Smaug HTTP read contracts."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Annotated

import httpx
from mcp.server import MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from mcp.types import ToolAnnotations
from pydantic import Field, JsonValue

from smaug.shared.config import Settings, get_settings
from smaug.shared.mcp_api import JsonObject, McpApiError, McpApiReader

type Offset = Annotated[int, Field(ge=0, description="Records to skip.")]
type Limit = Annotated[int, Field(ge=1, le=500, description="Page size (maximum 500).")]


def create_server(
    settings: Settings, *, transport: httpx.AsyncBaseTransport | None = None
) -> MCPServer[None]:
    """Compose tools and own a single HTTP client for the server lifespan."""
    reader: McpApiReader | None = None

    @asynccontextmanager
    async def lifespan(_server: MCPServer[None]) -> AsyncIterator[None]:
        nonlocal reader
        async with httpx.AsyncClient(
            base_url=settings.mcp_api_base_url.rstrip("/") + "/",
            timeout=settings.mcp_api_timeout_seconds,
            transport=transport,
            follow_redirects=False,
        ) as client:
            reader = McpApiReader(client)
            try:
                yield None
            finally:
                reader = None

    server: MCPServer[None] = MCPServer(
        "smaug",
        version="0.1.0",
        instructions=(
            "Query persisted Smaug CVM/B3 data. Values are not live quotes. "
            "Preserve named null reasons, source dates and price bases. "
            "Use next_offset to continue lists; pagination is applied locally "
            "to each fresh API response. No tool computes financial indicators."
        ),
        lifespan=lifespan,
    )
    annotations = ToolAnnotations(read_only_hint=True, open_world_hint=False)

    def api() -> McpApiReader:
        if reader is None:
            raise ToolError("api_unavailable: MCP HTTP client is not initialized")
        return reader

    async def read_list(path: str, offset: int, limit: int) -> JsonObject:
        try:
            return await api().read_list(path, offset, limit)
        except McpApiError as exc:
            raise ToolError(str(exc)) from exc

    async def read_ticker(
        endpoint: str, ticker: str, collection: str, offset: int, limit: int
    ) -> JsonObject:
        try:
            return await api().read_ticker(endpoint, ticker, collection, offset, limit)
        except McpApiError as exc:
            raise ToolError(str(exc)) from exc

    @server.tool(annotations=annotations)
    async def list_portfolio(
        offset: Offset = 0, limit: Limit = 100
    ) -> dict[str, JsonValue]:
        """List favorite tickers in API order, with pagination."""
        return await read_list("portfolio", offset, limit)

    @server.tool(annotations=annotations)
    async def list_analysis_summaries(
        offset: Offset = 0, limit: Limit = 100
    ) -> dict[str, JsonValue]:
        """List persisted TTM summaries; absent values retain API nulls."""
        return await read_list("analysis/summary", offset, limit)

    @server.tool(annotations=annotations)
    async def get_ticker_analysis(
        ticker: str, offset: Offset = 0, limit: Limit = 100
    ) -> dict[str, JsonValue]:
        """Read full TTM and paginated annual analyses, contracts and provenance."""
        return await read_ticker("analysis", ticker, "history", offset, limit)

    @server.tool(annotations=annotations)
    async def get_price_history(
        ticker: str, offset: Offset = 0, limit: Limit = 100
    ) -> dict[str, JsonValue]:
        """Read B3 closes in API order, preserving traded/adjusted bases and gaps."""
        return await read_ticker("prices", ticker, "points", offset, limit)

    @server.tool(annotations=annotations)
    async def get_dividend_history(
        ticker: str, offset: Offset = 0, limit: Limit = 100
    ) -> dict[str, JsonValue]:
        """Read B3 cash events and payment dates with original coverage reasons."""
        return await read_ticker("dividends", ticker, "events", offset, limit)

    @server.tool(annotations=annotations)
    async def get_shareholding_position(
        ticker: str, offset: Offset = 0, limit: Limit = 100
    ) -> dict[str, JsonValue]:
        """Read the latest mirrored CVM shareholder positions and filing metadata."""
        return await read_ticker("shareholders", ticker, "positions", offset, limit)

    return server


def main() -> None:
    """Run the local MCP server with protocol-only stdout and stderr logging."""
    create_server(get_settings()).run(transport="stdio")


if __name__ == "__main__":
    main()
