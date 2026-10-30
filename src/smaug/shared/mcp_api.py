"""Read-only HTTP adapter and transport pagination for the local MCP server."""

from __future__ import annotations

import json
import re

import httpx
from pydantic import JsonValue, TypeAdapter, ValidationError

from smaug.shared.mcp_setup import SETUP_GUIDE_HINT, SETUP_GUIDE_URI

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

    async def _request(self, path: str) -> httpx.Response:
        try:
            response = await self._client.get(path)
        except httpx.TimeoutException as exc:
            raise McpApiError(
                f"api_timeout: Smaug API did not respond in time. {SETUP_GUIDE_HINT}"
            ) from exc
        except httpx.RequestError as exc:
            raise McpApiError(
                "api_unavailable: start the Smaug API and check MCP_API_BASE_URL. "
                f"{SETUP_GUIDE_HINT}"
            ) from exc
        return response

    async def _get(self, path: str) -> JsonValue:
        response = await self._request(path)
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
            if response.status_code >= 500:
                message += f"; {SETUP_GUIDE_HINT}"
            raise McpApiError(message)
        return self._decode(response)

    @staticmethod
    def _decode(response: httpx.Response) -> JsonValue:
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

    async def get_service_status(self) -> JsonObject:
        """Read API liveness/readiness and retain partial diagnostic results."""
        result: JsonObject = {
            "api_base_url": str(self._client.base_url),
            "setup_guide_uri": SETUP_GUIDE_URI,
            "health": None,
            "readiness": None,
        }
        try:
            for path, key in (("health", "health"), ("ready", "readiness")):
                response = await self._request(path)
                result[f"{key}_http_status"] = response.status_code
                if response.status_code not in (
                    (200,) if path == "health" else (200, 503)
                ):
                    raise McpApiError(
                        f"api_http_error: {path} returned HTTP {response.status_code}"
                    )
                payload = self._decode(response)
                if not isinstance(payload, dict):
                    raise McpApiError(f"invalid_api_response: expected {path} object")
                result[key] = payload
                if path == "health":
                    if payload.get("status") != "ok" or not isinstance(
                        payload.get("commit"), str
                    ):
                        raise McpApiError(
                            "invalid_api_response: invalid health contract"
                        )
                else:
                    allowed = {
                        "mongo": {"ok", "unavailable"},
                        "postgres": {"ok", "unavailable"},
                        "schema": {"ok", "migration_required", "unavailable"},
                        "data": {"prepared", "empty", "unavailable"},
                    }
                    ready = payload.get("ready")
                    valid = isinstance(ready, bool) and all(
                        isinstance(payload.get(field), str) and payload[field] in values
                        for field, values in allowed.items()
                    )
                    dependencies_ready = all(
                        payload.get(field) == "ok"
                        for field in ("mongo", "postgres", "schema")
                    ) and payload.get("data") in ("prepared", "empty")
                    if (
                        not valid
                        or ready != (response.status_code == 200)
                        or ready != dependencies_ready
                    ):
                        raise McpApiError(
                            "invalid_api_response: inconsistent readiness contract"
                        )
                    result["status"] = (
                        ("ready_empty" if payload["data"] == "empty" else "ready")
                        if ready
                        else (
                            "migration_required"
                            if payload["schema"] == "migration_required"
                            else "dependencies_unavailable"
                        )
                    )
        except McpApiError as exc:
            result["status"] = str(exc).split(":", 1)[0]
            result["error"] = str(exc)
        status = result["status"]
        result["next_steps"] = {
            "ready": "API is ready. Query persisted data with the data tools.",
            "ready_empty": (
                "API is ready with no analysis data. Follow get_setup_guide "
                "to resume automatic preparation with smaug installer."
            ),
            "migration_required": (
                "Inspect smaug check and logs api; use smaug upgrade "
                "to apply the release migrations."
            ),
        }.get(
            str(status),
            "Check MCP_API_BASE_URL, smaugctl status/check and smaugctl logs api. "
            "Use get_setup_guide for start and recovery instructions.",
        )
        return result

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
