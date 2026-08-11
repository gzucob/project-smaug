"""Resolve a B3 company without trusting a trading root as permanent identity."""

from __future__ import annotations

import base64
import json
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

import httpx

from smaug.shared.logging import get_logger

logger = get_logger(__name__)

_USER_AGENT = "Mozilla/5.0"
_ROOT_LENGTH = 4


@dataclass(frozen=True)
class B3ListedCompany:
    """The current B3 supplement reached from one ticker's CVM registrant."""

    requested_root: str
    issuing_company: str
    trading_name: str
    cvm_code: str | None
    supplement: Mapping[str, Any]


class B3CompanyResolutionError(Exception):
    """A source-validation finding raised before a wrong company can be read."""

    def __init__(
        self,
        code: str,
        detail: str,
        *,
        evidence: Mapping[str, object] | None = None,
    ) -> None:
        super().__init__(detail)
        self.code = code
        self.detail = detail
        self.evidence = evidence or {}


class B3ListedCompanyResolver:
    """Resolve the current supplement by root, then by stable CVM code."""

    def __init__(self, http_client: httpx.AsyncClient, *, base_url: str) -> None:
        self._http = http_client
        self._base_url = base_url.rstrip("/")

    async def resolve(self, ticker: str, *, cvm_code: str | None) -> B3ListedCompany:
        """Return a registrant-verified supplement for ``ticker``."""
        requested_root = ticker.strip().upper()[:_ROOT_LENGTH]
        expected_code = _text(cvm_code)
        initial = await self._supplement(requested_root)
        if initial is not None and self._matches(initial, expected_code):
            trading_name = _text(initial.get("tradingName"))
            if trading_name:
                return self._resolved(
                    requested_root,
                    requested_root,
                    initial,
                    expected_code=expected_code,
                )

        if not expected_code:
            raise B3CompanyResolutionError(
                "coverage-established",
                "B3 supplement is absent or cannot be tied to a CVM registrant",
                evidence={"supplement": dict(initial or {})},
            )

        detail = await self._detail(expected_code)
        if detail is None:
            raise B3CompanyResolutionError(
                "coverage-established",
                f"B3 names no listed company for CVM registrant {expected_code}",
                evidence={"supplement": dict(initial or {})},
            )
        detail_code = _text(detail.get("codeCVM"))
        if not _same_code(detail_code, expected_code):
            raise B3CompanyResolutionError(
                "response-schema",
                "B3 detail returned a different CVM registrant",
                evidence={"detail": dict(detail)},
            )
        current_root = _text(detail.get("issuingCompany")).upper()
        if not current_root:
            raise B3CompanyResolutionError(
                "response-schema",
                "B3 detail lacks an issuingCompany",
                evidence={"detail": dict(detail)},
            )
        current = await self._supplement(current_root)
        if current is None:
            raise B3CompanyResolutionError(
                "coverage-established",
                f"B3 detail resolves {requested_root} to {current_root}, "
                "but its supplement is absent",
                evidence={"detail": dict(detail)},
            )
        return self._resolved(
            requested_root,
            current_root,
            current,
            expected_code=expected_code,
        )

    def _resolved(
        self,
        requested_root: str,
        current_root: str,
        supplement: Mapping[str, Any],
        *,
        expected_code: str,
    ) -> B3ListedCompany:
        published_code = _text(supplement.get("codeCVM"))
        if expected_code and not _same_code(published_code, expected_code):
            raise B3CompanyResolutionError(
                "response-schema",
                "B3 supplement returned a different CVM registrant",
                evidence={"supplement": dict(supplement)},
            )
        published_root = _text(supplement.get("code")).upper()
        if published_root and published_root != current_root:
            raise B3CompanyResolutionError(
                "response-schema",
                "B3 supplement returned a different issuing company",
                evidence={"supplement": dict(supplement)},
            )
        trading_name = _text(supplement.get("tradingName"))
        if not trading_name:
            raise B3CompanyResolutionError(
                "response-schema",
                "B3 supplement lacks a tradingName",
                evidence={"supplement": dict(supplement)},
            )
        return B3ListedCompany(
            requested_root=requested_root,
            issuing_company=current_root,
            trading_name=trading_name,
            cvm_code=expected_code or published_code or None,
            supplement=supplement,
        )

    @staticmethod
    def _matches(supplement: Mapping[str, Any], expected_code: str) -> bool:
        if not expected_code:
            return True
        return _same_code(_text(supplement.get("codeCVM")), expected_code)

    async def _supplement(self, root: str) -> Mapping[str, Any] | None:
        body = await self._get(
            "GetListedSupplementCompany",
            {"issuingCompany": root, "language": "pt-br"},
            identity=root,
        )
        if isinstance(body, list):
            body = body[0] if body else None
        if body is None:
            return None
        if not isinstance(body, Mapping):
            raise B3CompanyResolutionError(
                "response-schema",
                "B3 supplement response is not a JSON object",
                evidence={"response": body},
            )
        if not body:
            return None
        return body

    async def _detail(self, cvm_code: str) -> Mapping[str, Any] | None:
        body = await self._get(
            "GetDetail",
            {"codeCVM": cvm_code, "language": "pt-br"},
            identity=cvm_code,
        )
        if body is None:
            return None
        if not isinstance(body, Mapping):
            raise B3CompanyResolutionError(
                "response-schema",
                "B3 detail response is not a JSON object",
                evidence={"response": body},
            )
        if not body:
            return None
        return body

    async def _get(
        self, endpoint: str, params: dict[str, object], *, identity: str
    ) -> object | None:
        url = f"{self._base_url}/{endpoint}/{_encoded(params)}"
        try:
            response = await self._http.get(
                url, headers={"User-Agent": _USER_AGENT}, timeout=30.0
            )
        except httpx.HTTPError as exc:
            logger.warning("B3 %s failed for %s: %s", endpoint, identity, exc)
            raise B3CompanyResolutionError(
                "coverage-established", f"B3 {endpoint} failed: {exc}"
            ) from exc
        if response.status_code != httpx.codes.OK:
            raise B3CompanyResolutionError(
                "coverage-established",
                f"B3 {endpoint} returned HTTP {response.status_code}",
            )
        if not response.text.strip():
            return None
        try:
            parsed: object = response.json()
            return parsed
        except ValueError as exc:
            raise B3CompanyResolutionError(
                "response-schema", f"B3 {endpoint} response is not JSON"
            ) from exc


def _same_code(left: str, right: str) -> bool:
    return (left.lstrip("0") or "0") == (right.lstrip("0") or "0")


def _text(value: object) -> str:
    return str(value).strip() if value is not None else ""


def _encoded(params: dict[str, object]) -> str:
    return base64.b64encode(json.dumps(params).encode()).decode()
