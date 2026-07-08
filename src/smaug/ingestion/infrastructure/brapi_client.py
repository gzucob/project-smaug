"""Async brapi HTTP client with the minimal resilience the plan asks for.

One responsibility: *fetch* one module for one ticker and hand back the raw
response. It does not save, does not interpret. Documented status codes are
mapped to typed errors so the use case can decide stop/wait/skip (plan §5.1).

The auth token is NEVER stored in the returned ``request`` metadata — the
repo is public and that metadata gets persisted.
"""

from __future__ import annotations

from collections.abc import Sequence

import httpx

from smaug.ingestion.domain.ports import RawFetchResult
from smaug.shared.errors import (
    BrapiAuthError,
    BrapiForbiddenError,
    BrapiNotFoundError,
    BrapiRateLimitError,
    BrapiUnexpectedStatusError,
)

# brapi requests dividends via a flag, not the ``modules=`` param.
DIVIDENDS_MODULE = "dividends"


class BrapiClient:
    """Thin wrapper over ``httpx.AsyncClient`` for the brapi quote endpoint."""

    def __init__(
        self, base_url: str, token: str, http_client: httpx.AsyncClient
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._token = token
        self._http = http_client

    async def fetch(self, ticker: str, module: str) -> Sequence[RawFetchResult]:
        """Fetch one ``module`` for ``ticker``. Raises typed errors on failure.

        brapi answers with a single payload, so the sequence always has one item
        (the port allows several — see ``RawDataSource`` — for the CVM quarters).
        """
        url = f"{self._base_url}/quote/{ticker}"
        # Token goes on the wire but is kept out of the audit metadata.
        query = {"token": self._token, **self._module_params(module)}
        audit_params = self._module_params(module)

        response = await self._http.get(url, params=query)
        self._raise_for_status(response, ticker, module)

        return [
            RawFetchResult(
                module=module,
                request={"url": url, "params": audit_params},
                http_status=response.status_code,
                payload=response.json(),
            )
        ]

    @staticmethod
    def _module_params(module: str) -> dict[str, str]:
        if module == DIVIDENDS_MODULE:
            return {"dividends": "true"}
        return {"modules": module}

    @staticmethod
    def _raise_for_status(response: httpx.Response, ticker: str, module: str) -> None:
        status = response.status_code
        if status == httpx.codes.OK:
            return
        where = f"{ticker}/{module}"
        if status == httpx.codes.UNAUTHORIZED:
            raise BrapiAuthError(f"401 Unauthorized for {where}: check BRAPI_TOKEN")
        if status in (httpx.codes.PAYMENT_REQUIRED, httpx.codes.TOO_MANY_REQUESTS):
            raise BrapiRateLimitError(f"{status} plan/rate limit hit at {where}")
        if status == httpx.codes.FORBIDDEN:
            raise BrapiForbiddenError(
                f"403 forbidden for {where}: plan-restricted (upgrade required)"
            )
        if status == httpx.codes.NOT_FOUND:
            raise BrapiNotFoundError(f"404 not found for {where}")
        raise BrapiUnexpectedStatusError(
            status, f"unexpected {status} for {where}: {response.text[:200]}"
        )
