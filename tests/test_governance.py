"""Governance evidence preserves security identity, dates, and unresolved rights."""

from collections.abc import Mapping
from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Any

import pytest

from smaug.analysis.domain.calculator import compute
from smaug.analysis.domain.entities import TickerAnalysis
from smaug.analysis.domain.financials import MarketData, StandardizedFinancials
from smaug.analysis.domain.governance import Governance, segment_tag_along
from smaug.analysis.domain.indicators import (
    CALCULATION_CONTRACT_VERSION,
    GENERAL_INDICATORS_V1,
    Indicators,
    NullReason,
    indicator_contracts,
)
from smaug.analysis.infrastructure.mongo_governance import MongoGovernanceReader
from smaug.analysis.infrastructure.sql_repository import _to_entity, _to_row
from smaug.entrypoints.api import _to_response
from smaug.portfolio.domain.sectors import Sector
from smaug.portfolio.domain.share_classes import PerShareClass, UnitComponent
from smaug.portfolio.domain.taxonomy import Classification

ON = (UnitComponent(1, PerShareClass.ORDINARY),)
PN = (UnitComponent(1, PerShareClass.PREFERRED),)
UNIT = (*ON, *PN)


class _Cursor:
    def __init__(self, rows: list[dict[str, Any]]) -> None:
        self.rows = rows

    async def to_list(self, _length: int | None) -> list[dict[str, Any]]:
        return self.rows


class _Collection:
    def __init__(self, rows: list[dict[str, Any]]) -> None:
        self.rows = rows

    def find(self, query: Mapping[str, Any], /) -> _Cursor:
        return _Cursor(
            [row for row in self.rows if all(row.get(k) == v for k, v in query.items())]
        )


def _listing(
    segment: str = "NM", observed: str = "2026-10-04T12:00:00+00:00"
) -> dict[str, Any]:
    return {
        "module": "LISTING_B3",
        "source": "b3",
        "cvm_code": "42",
        "payload": {"observed_at": observed, "supplement": {"segment": segment}},
    }


def _rights(
    species: str, percent: str, *, version: str = "1", subclass: str = ""
) -> dict[str, Any]:
    return {
        "module": "TAG_ALONG",
        "source": "cvm",
        "cvm_code": "42",
        "payload": {
            "filing": {"DT_RECEB": "2022-05-31"},
            "Data_Referencia": "2022-01-01",
            "Versao": version,
            "ID_Documento": "10",
            "Especie_Acao": species,
            "Classe_Acao_Preferencial": subclass,
            "Percentual_Tag_Along": percent,
        },
    }


def _reader(rows: list[dict[str, Any]]) -> MongoGovernanceReader:
    return MongoGovernanceReader(_Collection(rows), registrant_resolver=lambda _: "42")


@pytest.mark.parametrize(
    ("segment", "classes", "expected"),
    [
        ("novo_mercado", ("ON",), Decimal(1)),
        ("novo_mercado", ("PN",), None),
        ("nivel_2", ("ON", "PN"), Decimal(1)),
        ("nivel_1", ("ON",), None),
        ("bovespa_mais", ("ON",), Decimal(1)),
        ("bovespa_mais", ("PN",), None),
        ("bovespa_mais_nivel_2", ("ON", "PNA"), Decimal(1)),
        ("basico", ("PN",), None),
        ("nivel_2", (), None),
    ],
)
def test_segment_guarantee_does_not_replace_unproved_statutory_rights(
    segment: str, classes: tuple[str, ...], expected: Decimal | None
) -> None:
    assert segment_tag_along(segment, classes) == expected


async def test_current_observation_cannot_classify_an_earlier_period() -> None:
    reader = _reader([_listing()])
    current = await reader.read("TEST3", date(2026, 10, 4), ON)
    assert current.listing_segment == "novo_mercado"
    assert current.tag_along == Decimal(1)
    assert current.listing_observed_on == date(2026, 10, 4)
    earlier = await reader.read("TEST3", date(2026, 6, 30), ON)
    assert earlier.listing_segment is None
    assert earlier.tag_along is None


async def test_filed_rights_resolve_each_species_and_do_not_survive_into_today() -> (
    None
):
    reader = _reader([_rights("Ordinária", "100"), _rights("Preferencial", "80")])
    assert (await reader.read("TEST3", date(2022, 12, 31), ON)).tag_along == Decimal(1)
    assert (await reader.read("TEST4", date(2022, 12, 31), PN)).tag_along == Decimal(
        "0.8"
    )
    unit = await reader.read("TEST11", date(2022, 12, 31), UNIT)
    assert unit.tag_along is None
    assert unit.blocker == "unresolved_tag_along_classes"
    assert (await reader.read("TEST3", date(2026, 10, 4), ON)).tag_along is None


async def test_latest_filed_version_and_exact_preferred_subclass_are_required() -> None:
    reader = _reader(
        [
            _rights("Preferencial", "80", subclass="Preferencial Classe A"),
            _rights(
                "Preferencial", "100", subclass="Preferencial Classe A", version="2"
            ),
        ]
    )
    pna = (UnitComponent(1, PerShareClass.PREFERRED_A),)
    pnb = (UnitComponent(1, PerShareClass.PREFERRED_B),)
    assert (await reader.read("TEST5", date(2022, 12, 31), pna)).tag_along == Decimal(1)
    assert (await reader.read("TEST6", date(2022, 12, 31), pnb)).tag_along is None


@pytest.mark.parametrize("raw", ["", "NaN", "-1", "101", "invalid"])
async def test_invalid_filed_percentage_never_becomes_zero(raw: str) -> None:
    evidence = await _reader([_rights("Ordinária", raw)]).read(
        "TEST3", date(2022, 12, 31), ON
    )
    assert evidence.tag_along is None


async def test_conflicting_or_foreign_evidence_cannot_publish_a_percentage() -> None:
    listing = _listing("NM", "2022-10-04T12:00:00+00:00")
    result = await _reader([listing, _rights("Ordinária", "80")]).read(
        "TEST3", date(2022, 12, 31), ON
    )
    assert result.tag_along is None
    assert result.blocker == "conflicting_tag_along_evidence"
    foreign = {**_listing(), "cvm_code": "99"}
    assert (
        await _reader([foreign]).read("TEST3", date(2026, 10, 4), ON)
    ).listing_segment is None


def test_zero_right_is_a_value_and_missing_right_has_a_named_cause() -> None:
    financials = StandardizedFinancials(
        reference_date=date(2022, 12, 31),
        sector=Sector.INDUSTRY,
        tag_along=Decimal(0),
    )
    assert compute(financials, None, MarketData()).tag_along == Decimal(0)
    absent = StandardizedFinancials(
        reference_date=date(2022, 12, 31),
        sector=Sector.INDUSTRY,
        tag_along_null_reason=NullReason.MISSING_TAG_ALONG_EVIDENCE,
    )
    assert (
        compute(absent, None, MarketData()).null_reasons["tag_along"]
        == NullReason.MISSING_TAG_ALONG_EVIDENCE
    )


def test_governance_round_trips_through_sql_and_api() -> None:
    evidence = Governance(
        listing_segment="novo_mercado",
        listing_observed_on=date(2026, 10, 4),
        listing_source="b3",
        tag_along=Decimal(1),
        tag_along_source="b3_listing_rules",
        tag_along_reference="segment=novo_mercado",
        blocker=None,
    )
    analysis = TickerAnalysis(
        ticker="TEST3",
        classification=Classification("Financeiro", None, None),
        reference_date=date(2026, 6, 30),
        computed_at=datetime(2026, 10, 4, tzinfo=UTC),
        indicators=Indicators(tag_along=Decimal(1)),
        governance=evidence,
        calculation_contract_version=CALCULATION_CONTRACT_VERSION,
    )
    restored = _to_entity(_to_row(analysis))
    assert restored.governance == evidence
    response = _to_response(restored)
    assert response.governance.listing_segment == "novo_mercado"
    assert response.indicators.tag_along == Decimal(1)
    assert "tag_along" not in indicator_contracts(GENERAL_INDICATORS_V1)


async def test_listing_guarantee_does_not_hide_a_conflicting_unit_component() -> None:
    reader = _reader(
        [
            _listing("N2", "2022-10-04T12:00:00+00:00"),
            _rights("Ordinária", "100"),
            _rights("Preferencial", "80"),
        ]
    )
    result = await reader.read("TEST11", date(2022, 12, 31), UNIT)
    assert result.tag_along is None
    assert result.blocker == "conflicting_tag_along_evidence"


@pytest.mark.parametrize(
    ("raw", "label"),
    [("BOLSA", "basico"), ("MP", "companhia_menor_porte"), ("UNKNOWN", None)],
)
async def test_listing_labels_never_infer_a_percentage(
    raw: str, label: str | None
) -> None:
    result = await _reader([_listing(raw)]).read("TEST3", date(2026, 10, 4), ON)
    assert result.listing_segment == label
    assert result.listing_source == "b3"
    assert result.tag_along is None


async def test_later_receipt_cannot_rewrite_the_earlier_filed_right() -> None:
    first = _rights("Ordinária", "80")
    later = _rights("Ordinária", "100", version="2")
    later["payload"]["filing"]["DT_RECEB"] = "2023-01-10"
    result = await _reader([first, later]).read("TEST3", date(2022, 12, 31), ON)
    assert result.tag_along == Decimal("0.8")
    assert "v1" in (result.tag_along_reference or "")


async def test_on_below_floor_is_unresolved_and_pn_zero_is_valid() -> None:
    reader = _reader([_rights("Ordinária", "0"), _rights("Preferencial", "0")])
    ordinary = await reader.read("TEST3", date(2022, 12, 31), ON)
    assert ordinary.tag_along is None
    assert ordinary.blocker == "conflicting_tag_along_evidence"
    preferred = await reader.read("TEST4", date(2022, 12, 31), PN)
    assert preferred.tag_along == Decimal(0)
    assert preferred.blocker is None


@pytest.mark.parametrize(
    ("code", "market", "expected"),
    [
        ("MB", "SOMA", "mercado_soma"),
        ("BOLSA", "BALCAO NAO ORG.", "balcao_nao_organizado"),
    ],
)
async def test_balcao_market_is_not_classified_as_basic_listing(
    code: str, market: str, expected: str
) -> None:
    doc = _listing(code)
    doc["payload"]["detail"] = {"market": market}
    value = await _reader([doc]).read("TEST3", date(2026, 10, 4), ON)
    assert value.listing_segment == expected
    assert value.tag_along is None
