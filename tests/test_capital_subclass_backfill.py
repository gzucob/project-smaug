"""Guardrails for the bounded CAPITAL parser-v2 replay in issue #306."""

from datetime import UTC, datetime

import pytest

from smaug.ingestion.domain.ports import RawFetchResult
from smaug.ingestion.domain.runs import ParserIdentity
from smaug.ingestion.infrastructure.capital_subclass_backfill import (
    AuditedCapitalSubclassSource,
    CapitalSubclassBackfillTarget,
)
from smaug.shared.errors import SourceMalformedError


class _Source:
    parser_identity = ParserIdentity("test", 1)

    def __init__(self, rows: tuple[RawFetchResult, ...]) -> None:
        self.rows = rows

    async def fetch(self, _ticker: str, _module: str) -> tuple[RawFetchResult, ...]:
        return self.rows


def _target() -> CapitalSubclassBackfillTarget:
    return CapitalSubclassBackfillTarget(
        ticker="TEST5",
        cd_cvm="1234",
        cnpj="12.345.678/0001-90",
        year=2020,
        artifact_id="sha256:audited",
        reference_date="2020-01-01",
        version=3,
        capital_id="42",
        approval_date="2021-04-30",
        filing_rows=1,
        versions=(3,),
        children=(("Preferencial Classe A", 10),),
    )


def _row(*, children: object) -> RawFetchResult:
    target = _target()
    return RawFetchResult(
        module="CAPITAL",
        source="cvm",
        request={},
        http_status=200,
        payload={
            "cnpj": target.cnpj,
            "reference_date": target.reference_date,
            "version": target.version,
            "capital_id": target.capital_id,
            "approval_date": target.approval_date,
            "share_class_counts": children,
            "fetched_at": datetime(2026, 9, 14, tzinfo=UTC),
        },
        cvm_code=target.cd_cvm,
        artifact_id=target.artifact_id,
    )


async def test_source_returns_only_the_exact_audited_parent_and_children() -> None:
    row = _row(children=[{"share_class": "Preferencial Classe A", "shares": 10}])
    source = AuditedCapitalSubclassSource(_Source((row,)), (_target(),))

    assert await source.fetch("TEST5", "CAPITAL") == [row]


async def test_source_stops_when_the_child_ledger_drifts() -> None:
    row = _row(children=[{"share_class": "Preferencial Classe A", "shares": 11}])
    source = AuditedCapitalSubclassSource(_Source((row,)), (_target(),))

    with pytest.raises(SourceMalformedError, match="#303 drift"):
        await source.fetch("TEST5", "CAPITAL")


async def test_source_stops_when_the_group_shape_drifts() -> None:
    row = _row(children=[{"share_class": "Preferencial Classe A", "shares": 10}])
    source = AuditedCapitalSubclassSource(_Source((row, row)), (_target(),))

    with pytest.raises(SourceMalformedError, match="expected rows=1"):
        await source.fetch("TEST5", "CAPITAL")
