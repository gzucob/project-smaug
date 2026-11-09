"""Read registrant-verified B3 listing evidence from the raw mirror."""

import re
from collections.abc import Mapping
from datetime import date
from typing import Any

from smaug.portfolio.infrastructure.fca_placeholders import (
    OfficialRegistrant,
    OfficialSecurityCode,
)
from smaug.shared.errors import SourceMalformedError, SourceNotFoundError
from smaug.shared.source_inputs import SourceCollection


class MongoRegistrantResolver:
    def __init__(self, collection: SourceCollection) -> None:
        self._collection = collection

    async def resolve_by_cvm(
        self, cvm_code: str, *, cnpj: str | None = None
    ) -> OfficialRegistrant:
        docs = await self._collection.find(
            {"source": "b3", "module": "LISTING_B3", "cvm_code": cvm_code}
        ).to_list(None)
        if not docs:
            raise SourceNotFoundError(f"missing_mirrored_listing:{cvm_code}")
        doc = max(
            docs,
            key=lambda item: (
                str(item["payload"].get("observed_at", "")),
                str(item.get("content_hash", "")),
            ),
        )
        payload = doc["payload"]
        detail = payload.get("detail") or {}
        supplement = payload.get("supplement") or {}
        for facts in (detail, supplement):
            for key in ("cnpj", "cnpjCompany", "cnpjEmpresa"):
                published = _text(facts.get(key))
                if published and cnpj and _digits(published) != _digits(cnpj):
                    raise SourceMalformedError("mirrored_listing_cnpj_mismatch")
            published_code = _text(facts.get("codeCVM"))
            if published_code and published_code.lstrip("0") != cvm_code.lstrip("0"):
                raise SourceMalformedError("mirrored_listing_cvm_mismatch")
        codes: dict[str, str | None] = {}
        other = detail.get("otherCodes", [])
        candidates = [
            detail,
            *(item for item in other if isinstance(item, Mapping)),
            supplement,
        ]
        for facts in candidates:
            code = _text(facts.get("code")).upper()
            if not re.fullmatch(r"[A-Z0-9]{4}[0-9]{1,2}", code) or code.isdigit():
                continue
            isin = next(
                (
                    _text(facts.get(key)).upper()
                    for key in ("isin", "isinCode", "codIsi", "CODISI")
                    if _text(facts.get(key))
                ),
                None,
            )
            if codes.get(code) is None or isin is not None:
                codes[code] = isin
        return OfficialRegistrant(
            cvm_code=cvm_code,
            cnpj=cnpj,
            issuing_company=_text(supplement.get("issuingCompany")),
            quotation_date=_quotation_date(detail.get("dateQuotation"))
            or _quotation_date(supplement.get("quotedPerSharSince")),
            market=_field(detail, supplement, "market"),
            venue=_field(detail, supplement, "venue"),
            security_codes=tuple(
                OfficialSecurityCode(code=code, isin=isin)
                for code, isin in codes.items()
            ),
        )


def _text(value: object) -> str:
    return str(value).strip() if value is not None else ""


def _digits(value: str) -> str:
    return "".join(character for character in value if character.isdigit())


def _quotation_date(value: object) -> date | None:
    try:
        day, month, year = (int(part) for part in _text(value).split("/", 2))
        return date(year, month, day)
    except (TypeError, ValueError):
        return None


def _field(
    detail: Mapping[str, Any], supplement: Mapping[str, Any], name: str
) -> str | None:
    return _text(detail.get(name)) or _text(supplement.get(name)) or None
