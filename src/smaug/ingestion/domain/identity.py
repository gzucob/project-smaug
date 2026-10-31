"""Deterministic content identities for the structural filing mirror."""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from hashlib import sha256
from typing import Any

from smaug.ingestion.domain.entities import RawIngestion


@dataclass(frozen=True, slots=True)
class FilingIdentity:
    """The source fact whose duplicate versions the mirror rejects.

    ``fetched_at`` and ``run_id`` deliberately do not participate: they describe
    a processing attempt, rather than the fact the source published. The request
    names one filing within an artifact; the payload is hashed separately so an
    amended filing remains a new append-only version.
    """

    source: str
    artifact_id: str | None
    registrant_key: str
    module: str
    filing_discriminator: str
    content_hash: str


def filing_identity(ingestion: RawIngestion) -> FilingIdentity:
    """Build the stable identity for one parsed source filing."""
    request = ingestion.request
    payload = ingestion.payload
    if ingestion.source == "b3" and ingestion.module == "LISTING_B3":
        # This timestamp is added by our adapter, not published by B3. Preserve
        # it in the stored snapshot while deduplicating identical source facts.
        request = {key: value for key, value in request.items() if key != "observed_at"}
        payload = {key: value for key, value in payload.items() if key != "observed_at"}
    return FilingIdentity(
        source=ingestion.source,
        artifact_id=ingestion.artifact_id,
        registrant_key=_registrant_key(ingestion),
        module=ingestion.module,
        filing_discriminator=_hash(request),
        content_hash=_hash(payload),
    )


def _registrant_key(ingestion: RawIngestion) -> str:
    code = ingestion.cvm_code
    if code is not None and code.strip():
        return f"cvm:{code.strip()}"
    return f"ticker:{ingestion.ticker.strip().upper()}"


def _hash(value: Mapping[str, Any]) -> str:
    canonical = json.dumps(
        _canonicalize(value),
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
        allow_nan=False,
    )
    return f"sha256:{sha256(canonical.encode()).hexdigest()}"


def _canonicalize(value: Any) -> Any:
    """Convert source-shaped values to an unambiguous JSON representation."""
    if isinstance(value, Mapping):
        return {str(key): _canonicalize(item) for key, item in value.items()}
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        return [_canonicalize(item) for item in value]
    if isinstance(value, datetime):
        return {"$datetime": value.isoformat()}
    if isinstance(value, date):
        return {"$date": value.isoformat()}
    if isinstance(value, Decimal):
        return {"$decimal": str(value)}
    return value
