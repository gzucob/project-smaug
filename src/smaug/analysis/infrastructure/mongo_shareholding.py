"""Read one current CVM shareholder-position filing from the raw mirror."""

from __future__ import annotations

from collections.abc import Mapping
from datetime import date
from decimal import Decimal, InvalidOperation
from typing import Protocol

from smaug.analysis.domain.shareholding import (
    ShareholderClassPosition,
    ShareholderPosition,
    ShareholdingSnapshot,
)
from smaug.analysis.infrastructure.mirror import mirror_filter
from smaug.portfolio.domain.company import RegistrantResolver

SHAREHOLDER_POSITION_MODULE = "SHAREHOLDER_POSITION"


class RawCollection(Protocol):
    """The subset of the Mongo collection this reader uses."""

    def find(self, filter: Mapping[str, object]) -> object: ...


class ValidationCollection(Protocol):
    """The latest source-batch validation lookup used by this reader."""

    async def find_one(
        self,
        filter: Mapping[str, object],
        *,
        sort: list[tuple[str, int]],
    ) -> Mapping[str, object] | None: ...


class MongoShareholdingReader:
    """Serve the direct shareholder positions from the latest filed FRE."""

    def __init__(
        self,
        collection: RawCollection,
        *,
        registrant_resolver: RegistrantResolver,
        validation_collection: ValidationCollection | None = None,
    ) -> None:
        self._collection = collection
        self._registrant = registrant_resolver
        self._validations = validation_collection

    async def latest(self, ticker: str) -> ShareholdingSnapshot | None:
        """Read the latest filing by archive year, reference date, and version."""
        cursor = self._collection.find(
            mirror_filter(
                ticker,
                self._registrant,
                source="cvm",
                module=SHAREHOLDER_POSITION_MODULE,
            )
        )
        candidates: list[tuple[tuple[int, date, int, str], Mapping[str, object]]] = []
        async for document in cursor:  # type: ignore[attr-defined]
            request = document.get("request")
            payload = document.get("payload")
            if not isinstance(request, Mapping) or not isinstance(payload, Mapping):
                return None
            filing_key = _filing_key(payload)
            if filing_key is None:
                # This module has no legacy data contract. A malformed row could
                # otherwise disappear from an apparently complete filing.
                return None
            candidates.append((filing_key, document))
        if not candidates:
            return None

        latest_key = max(key for key, _ in candidates)
        selected = sorted(
            (document for key, document in candidates if key == latest_key),
            key=lambda document: _source_row(document),
        )
        artifact_ids = sorted(
            {
                str(document["artifact_id"])
                for document in selected
                if document.get("artifact_id") is not None
            }
        )
        for artifact_id in artifact_ids:
            if not await self._batch_is_usable(artifact_id):
                return None

        by_source_row: dict[str, Mapping[str, object]] = {}
        for document in selected:
            request = document["request"]
            payload = document["payload"]
            if not isinstance(request, Mapping) or not isinstance(payload, Mapping):
                return None
            source_row = _text(request.get("source_row"))
            # Older mirror rows did not persist source_row for named holders.
            # Their request still distinguishes the related holder when present.
            if not source_row:
                source_row = ":".join(
                    (
                        _text(request.get("shareholder_id")),
                        _text(request.get("related_shareholder_id")),
                    )
                )
            if not source_row.strip(":"):
                return None
            previous = by_source_row.get(source_row)
            if previous is not None and previous != payload:
                # Two artifacts claim different positions under the same filed
                # identity. Do not choose one by ingestion order.
                return None
            by_source_row[source_row] = payload

        parsed = tuple(_position(payload) for payload in by_source_row.values())
        if any(position is None for position in parsed):
            # Do not turn a malformed position into an apparently complete list.
            return None
        positions = tuple(position for position in parsed if position is not None)
        if not positions:
            return None

        first_request = selected[0]["request"]
        first_payload = selected[0]["payload"]
        if not isinstance(first_request, Mapping) or not isinstance(
            first_payload, Mapping
        ):
            return None
        reference_date = _date(first_payload.get("reference_date"))
        version = _int(first_payload.get("version"))
        archive_year = _int(first_payload.get("archive_year"))
        document_id = _text(first_payload.get("document_id"))
        if (
            reference_date is None
            or version is None
            or archive_year is None
            or not document_id
        ):
            return None
        selected_artifact_id = next(iter(artifact_ids), None)
        return ShareholdingSnapshot(
            reference_date=reference_date,
            version=version,
            document_id=document_id,
            archive_year=archive_year,
            artifact_id=selected_artifact_id,
            positions=positions,
        )

    async def _batch_is_usable(self, artifact_id: str) -> bool:
        if self._validations is None:
            return True
        report = await self._validations.find_one(
            {
                "source": "cvm",
                "module": SHAREHOLDER_POSITION_MODULE,
                "artifact_id": artifact_id,
            },
            sort=[("recorded_at", -1)],
        )
        # Legacy rows may predate durable batch validation.
        return report is None or report.get("status") == "accepted"


def _filing_key(
    payload: Mapping[str, object],
) -> tuple[int, date, int, str] | None:
    archive_year = _int(payload.get("archive_year"))
    reference_date = _date(payload.get("reference_date"))
    version = _int(payload.get("version"))
    document_id = _text(payload.get("document_id"))
    if (
        archive_year is None
        or reference_date is None
        or version is None
        or not document_id
    ):
        return None
    return archive_year, reference_date, version, document_id


def _source_row(document: Mapping[str, object]) -> int:
    request = document.get("request")
    if isinstance(request, Mapping):
        return _int(request.get("source_row")) or 0
    return 0


def _position(payload: Mapping[str, object]) -> ShareholderPosition | None:
    fields = payload.get("filed_fields")
    if not isinstance(fields, Mapping):
        return None
    shareholder_id = _text(payload.get("shareholder_id"))
    name = _text(fields.get("Acionista"))
    if not shareholder_id or not name:
        return None
    related_id = _text(fields.get("ID_Acionista_Relacionado")) or None
    controller_value = _text(fields.get("Acionista_Controlador")).upper()
    controller = (
        True if controller_value == "S" else False if controller_value == "N" else None
    )
    class_positions = payload.get("share_class_positions")
    classes = (
        tuple(
            item
            for row in class_positions
            if isinstance(row, Mapping)
            if (item := _class_position(row)) is not None
        )
        if isinstance(class_positions, list)
        else ()
    )
    return ShareholderPosition(
        shareholder_id=shareholder_id,
        name=name,
        shareholder_type=_text(fields.get("Tipo_Pessoa_Acionista")) or None,
        related_shareholder_id=related_id,
        is_controller=controller,
        ordinary_shares=_decimal(fields.get("Quantidade_Acao_Ordinaria_Circulacao")),
        ordinary_percent=_decimal(fields.get("Percentual_Acao_Ordinaria_Circulacao")),
        preferred_shares=_decimal(
            fields.get("Quantidade_Acao_Preferencial_Circulacao")
        ),
        preferred_percent=_decimal(
            fields.get("Percentual_Acao_Preferencial_Circulacao")
        ),
        total_shares=_decimal(fields.get("Quantidade_Total_Acoes_Circulacao")),
        total_percent=_decimal(fields.get("Percentual_Total_Acoes_Circulacao")),
        last_change_date=_date(fields.get("Data_Ultima_Alteracao")),
        class_positions=classes,
    )


def _class_position(row: Mapping[str, object]) -> ShareholderClassPosition | None:
    share_class = _text(row.get("Tipo_Classe_Acao_Preferencial"))
    if not share_class:
        return None
    return ShareholderClassPosition(
        share_class=share_class,
        shares=_decimal(row.get("Quantidade_Acoes")),
        percent=_decimal(row.get("Percentual_Acoes")),
    )


def _text(value: object) -> str:
    return str(value).strip() if value is not None else ""


def _date(value: object) -> date | None:
    text = _text(value)
    if not text:
        return None
    try:
        return date.fromisoformat(text[:10])
    except ValueError:
        return None


def _decimal(value: object) -> Decimal | None:
    text = _text(value)
    if not text:
        return None
    try:
        value = Decimal(text.replace(",", "."))
    except InvalidOperation:
        return None
    return value if value.is_finite() else None


def _int(value: object) -> int | None:
    text = _text(value)
    if not text:
        return None
    try:
        return int(text)
    except ValueError:
        return None
