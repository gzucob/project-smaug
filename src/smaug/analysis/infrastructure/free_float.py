"""Select dated company-wide free-float evidence from the mirrored CVM FRE."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import date
from decimal import Decimal, InvalidOperation
from typing import Any

from smaug.analysis.domain.financials import (
    SourceAccountEvidence,
    SourceAccountRef,
    SourceAccountStatus,
)
from smaug.analysis.domain.indicators import NullReason


def filed_free_float(
    documents: Sequence[Mapping[str, Any]], end: date
) -> tuple[Decimal | None, SourceAccountEvidence]:
    """Select the newest filed assembly snapshot on or before the valuation date."""
    candidates: list[tuple[date, int, date, int, Mapping[str, Any]]] = []
    for position, document in enumerate(documents):
        if document.get("module") != "FREE_FLOAT":
            continue
        payload = document.get("payload")
        if not isinstance(payload, Mapping):
            continue
        try:
            reference = date.fromisoformat(str(payload.get("reference_date", "")))
            assembly = date.fromisoformat(
                str(payload.get("assembly_date") or reference)
            )
            version = int(payload.get("version", 0))
        except (ValueError, TypeError):
            continue
        # FRE reference dates may be the filing year's upcoming December.
        # The assembly date states the date of the published distribution.
        if reference.year <= end.year and assembly <= end:
            candidates.append((assembly, version, reference, position, document))
    if not candidates:
        return None, SourceAccountEvidence(
            field="free_float",
            statement="FRE",
            status=SourceAccountStatus.ABSENT,
            expected=("company_total_free_float", "dated_assembly_on_or_before_view"),
            blocker=NullReason.SOURCE_ACCOUNT_ABSENT,
            consumer_indicators=("free_float",),
        )
    assembly, _, _, _, document = max(
        candidates, key=lambda item: (item[2], item[1], item[0], item[3])
    )
    payload = document["payload"]
    percent: Decimal | None
    try:
        percent = Decimal(str(payload.get("company_free_float_percent", "")))
        if not percent.is_finite() or not Decimal(0) <= percent <= Decimal(100):
            percent = None
    except InvalidOperation:
        percent = None
    return (
        percent / Decimal(100) if percent is not None else None,
        SourceAccountEvidence(
            field="free_float",
            statement="FRE",
            status=SourceAccountStatus.DERIVED
            if percent is not None
            else SourceAccountStatus.PRESENT_UNREADABLE,
            expected=(
                "scope=company_total",
                f"assembly_date={assembly}",
                f"filing_reference_date={payload['reference_date']}",
                f"version={payload['version']}",
                f"artifact_id={document.get('artifact_id')}",
            ),
            found=(
                SourceAccountRef(
                    code="Percentual_Total_Acoes_Circulacao",
                    name="Percentual total de ações em circulação",
                    value=percent,
                ),
            )
            if percent is not None
            else (),
            formula="filed_total_free_float_percent / 100",
            blocker=NullReason.SOURCE_ACCOUNT_ABSENT if percent is None else None,
            consumer_indicators=("free_float",),
        ),
    )
