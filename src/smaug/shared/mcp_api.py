"""Read-only HTTP adapter and transport pagination for the local MCP server."""

from __future__ import annotations

import json
import re

import httpx
from pydantic import JsonValue, TypeAdapter, ValidationError

type JsonObject = dict[str, JsonValue]

_JSON: TypeAdapter[JsonValue] = TypeAdapter(JsonValue)


class McpApiError(Exception):
    """An anticipated API or input failure safe to report to the MCP client."""


def normalize_ticker(ticker: str) -> str:
    """Normalize a ticker without allowing URL path or query injection."""
    symbol = ticker.strip().upper()
    if re.fullmatch(r"[A-Z0-9]+", symbol) is None:
        raise McpApiError("invalid_ticker: use a nonempty alphanumeric ticker")
    return symbol


def _reject_constant(value: str) -> None:
    raise ValueError(f"Invalid JSON constant: {value}")


def paginate(items: list[JsonValue], offset: int, limit: int) -> JsonObject:
    """Slice an ordered response and report explicit continuation metadata."""
    if offset < 0 or not 1 <= limit <= 500:
        raise McpApiError("invalid_pagination: offset >= 0 and 1 <= limit <= 500")
    end = offset + limit
    return {
        "items": items[offset:end],
        "total": len(items),
        "offset": offset,
        "limit": limit,
        "next_offset": end if end < len(items) else None,
    }


class McpApiReader:
    """Read the fixed Smaug endpoints without invoking commands or writes."""

    def __init__(self, client: httpx.AsyncClient) -> None:
        self._client = client

    async def _get(self, path: str) -> JsonValue:
        try:
            response = await self._client.get(path)
        except httpx.TimeoutException as exc:
            raise McpApiError("api_timeout: Smaug API did not respond in time") from exc
        except httpx.RequestError as exc:
            raise McpApiError(
                "api_unavailable: start the Smaug API and check MCP_API_BASE_URL"
            ) from exc
        if response.status_code != 200:
            detail: JsonValue = None
            try:
                error = _JSON.validate_python(response.json(), strict=True)
                if isinstance(error, dict):
                    detail = error.get("detail")
            except (ValueError, ValidationError):
                pass
            message = f"api_http_error: HTTP {response.status_code}"
            if detail is not None:
                message += f"; {json.dumps(detail, ensure_ascii=False)}"
            raise McpApiError(message)
        try:
            # API decimals normally arrive as strings. Preserve numeric decimal
            # tokens too, without ever rounding them through a binary float.
            return _JSON.validate_python(
                json.loads(
                    response.text, parse_float=str, parse_constant=_reject_constant
                ),
                strict=True,
            )
        except (ValueError, ValidationError) as exc:
            raise McpApiError("invalid_api_response: expected valid JSON") from exc

    async def read_list(self, path: str, offset: int, limit: int) -> JsonObject:
        """Read and paginate one of the two supported top-level lists."""
        if path not in ("portfolio", "analysis/summary"):
            raise McpApiError("unsupported_api_path")
        payload = await self._get(path)
        if not isinstance(payload, list) or not all(
            isinstance(item, dict) for item in payload
        ):
            raise McpApiError("invalid_api_response: expected a list of objects")
        return paginate(payload, offset, limit)

    async def read_ticker(
        self,
        endpoint: str,
        ticker: str,
        collection: str,
        offset: int,
        limit: int,
    ) -> JsonObject:
        """Keep response metadata intact while paginating its record collection."""
        routes = {
            "analysis": ("analysis/{ticker}", "history"),
            "prices": ("prices/{ticker}/history", "points"),
            "dividends": ("dividends/{ticker}/history", "events"),
            "shareholders": ("shareholders/{ticker}/position", "positions"),
        }
        route = routes.get(endpoint)
        if route is None or route[1] != collection:
            raise McpApiError("unsupported_api_path")
        symbol = normalize_ticker(ticker)
        payload = await self._get(route[0].format(ticker=symbol))
        if not isinstance(payload, dict) or payload.get("ticker") != symbol:
            raise McpApiError("invalid_api_response: expected matching ticker object")
        records = payload.get(collection)
        if not isinstance(records, list) or not all(
            isinstance(item, dict) for item in records
        ):
            raise McpApiError("invalid_api_response: expected a list of objects")
        if endpoint == "analysis" and (
            "ttm" not in payload
            or (payload["ttm"] is not None and not isinstance(payload["ttm"], dict))
        ):
            raise McpApiError("invalid_api_response: expected ttm object or null")
        page = paginate(records, offset, limit)
        return {**payload, collection: page.pop("items"), "pagination": page}
