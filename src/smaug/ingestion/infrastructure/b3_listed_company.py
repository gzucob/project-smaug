"""Resolve a B3 company without trusting a trading root as permanent identity."""

from __future__ import annotations

import base64
import json
import re
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date
from typing import Any

import httpx

from smaug.shared.logging import get_logger

logger = get_logger(__name__)

_USER_AGENT = "Mozilla/5.0"
_ROOT_LENGTH = 4
_SECURITY_CODE = re.compile(r"^[A-Z0-9]{4}[0-9]{1,2}$")


@dataclass(frozen=True)
class B3ListedCompany:
    """The current B3 supplement reached from one ticker's CVM registrant."""

    requested_root: str
    issuing_company: str
    trading_name: str
    cvm_code: str | None
    supplement: Mapping[str, Any]
    detail: Mapping[str, Any] | None = None
    quotation_date: date | None = None


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

    async def resolve_by_cvm(
        self, cvm_code: str, *, cnpj: str | None = None
    ) -> B3ListedCompany:
        """Resolve a registrant through ``GetDetail`` and its issuing root.

        The CVM code is the stable key.  The root returned by B3 is only a
        routing value to its supplement; no security code is inferred from the
        root itself.  When B3 publishes a CNPJ, it must also agree with the FCA
        registrant supplied by the caller.
        """
        expected_code = _text(cvm_code)
        if not expected_code:
            raise B3CompanyResolutionError(
                "coverage-established",
                "cannot query B3 without the FCA CD_CVM registrant key",
            )
        detail = await self._detail(expected_code)
        if detail is None:
            raise B3CompanyResolutionError(
                "coverage-established",
                f"B3 names no listed company for CVM registrant {expected_code}",
            )
        detail_code = _text(detail.get("codeCVM"))
        if not _same_code(detail_code, expected_code):
            raise B3CompanyResolutionError(
                "response-schema",
                "B3 detail returned a different CVM registrant",
                evidence={"detail": dict(detail)},
            )
        _validate_cnpj(detail, cnpj)
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
                f"B3 detail resolves {current_root} to a missing supplement",
                evidence={"detail": dict(detail)},
            )
        return self._resolved(
            current_root,
            current_root,
            current,
            expected_code=expected_code,
            expected_cnpj=cnpj,
            detail=detail,
        )

    async def resolve_registrant(
        self, cvm_code: str, *, cnpj: str | None = None
    ) -> B3ListedCompany:
        """Alias naming the stable FCA-to-B3 resolution operation."""
        return await self.resolve_by_cvm(cvm_code, cnpj=cnpj)

    def _resolved(
        self,
        requested_root: str,
        current_root: str,
        supplement: Mapping[str, Any],
        *,
        expected_code: str,
        expected_cnpj: str | None = None,
        detail: Mapping[str, Any] | None = None,
    ) -> B3ListedCompany:
        published_code = _text(supplement.get("codeCVM"))
        if expected_code and not _same_code(published_code, expected_code):
            raise B3CompanyResolutionError(
                "response-schema",
                "B3 supplement returned a different CVM registrant",
                evidence={"supplement": dict(supplement)},
            )
        _validate_cnpj(supplement, expected_cnpj)
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
            detail=detail,
            quotation_date=_quotation_date(detail.get("dateQuotation"))
            if detail is not None
            else None,
        )

    def official_codes(
        self, company: B3ListedCompany
    ) -> tuple[tuple[str, str | None], ...]:
        """Return security codes B3 explicitly publishes for the registrant.

        ``issuingCompany`` is deliberately excluded.  Only ``code`` values
        from ``GetDetail.otherCodes`` and the detail's own security code can be
        candidates for COTAHIST observation.
        """
        detail = company.detail or {}
        values: list[tuple[str, str | None]] = []
        own = _security_code(detail.get("code"))
        if own is not None:
            values.append((own, _isin(detail)))
        other_codes = detail.get("otherCodes")
        if isinstance(other_codes, list):
            for item in other_codes:
                if not isinstance(item, Mapping):
                    continue
                code = _security_code(item.get("code"))
                if code is not None:
                    values.append((code, _isin(item)))
        # A supplement may be the only place the current security is named in
        # older B3 responses.  It is still an official security code; the root
        # itself never reaches this list.
        supplement_code = _security_code(company.supplement.get("code"))
        if supplement_code is not None:
            values.append((supplement_code, _isin(company.supplement)))
        unique: dict[str, str | None] = {}
        for code, isin in values:
            current = unique.get(code)
            if current is None or isin is not None:
                unique[code] = isin
        return tuple(unique.items())

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


def _validate_cnpj(published: Mapping[str, Any], expected: str | None) -> None:
    """Reject a B3 response that publishes another registrant's CNPJ."""
    if not expected:
        return
    published_value = ""
    for key in ("cnpj", "cnpjCompany", "cnpjEmpresa"):
        value = _text(published.get(key))
        if value:
            published_value = value
            break
    if published_value and _digits(published_value) != _digits(expected):
        raise B3CompanyResolutionError(
            "response-schema",
            "B3 response returned a different CNPJ registrant",
            evidence={"response": dict(published)},
        )


def _digits(value: str) -> str:
    return "".join(character for character in value if character.isdigit())


def _security_code(value: object) -> str | None:
    code = _text(value).upper()
    if not _SECURITY_CODE.fullmatch(code) or code.isdigit():
        return None
    return code


def _isin(value: Mapping[str, Any]) -> str | None:
    for key in ("isin", "isinCode", "codIsi", "CODISI"):
        candidate = _text(value.get(key)).upper()
        if candidate:
            return candidate
    return None


def _text(value: object) -> str:
    return str(value).strip() if value is not None else ""


def _quotation_date(value: object) -> date | None:
    """Parse B3's ``GetDetail.dateQuotation`` (``DD/MM/YYYY``)."""
    text = _text(value)
    if not text:
        return None
    try:
        day, month, year = (int(part) for part in text.split("/", 2))
        return date(year, month, day)
    except (TypeError, ValueError):
        return None


def _encoded(params: dict[str, object]) -> str:
    return base64.b64encode(json.dumps(params).encode()).decode()
