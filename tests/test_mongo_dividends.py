"""B3 cash rights are read per share class on their filed quotation scale."""

from datetime import date
from decimal import Decimal
from typing import Any

from smaug.analysis.infrastructure.mongo_dividends import MongoCashEventReader
from smaug.portfolio.domain.share_classes import PerShareClass


class FakeCursor:
    def __init__(self, documents: list[dict[str, Any]]) -> None:
        self._documents = documents

    async def __aiter__(self) -> Any:
        for document in self._documents:
            yield document


class FakeCollection:
    def __init__(self, documents: list[dict[str, Any]]) -> None:
        self._documents = documents
        self.queries: list[dict[str, object]] = []

    def find(self, query: dict[str, object], /) -> FakeCursor:
        self.queries.append(query)
        matched = [
            document
            for document in self._documents
            if all(document.get(key) == value for key, value in query.items())
        ]
        return FakeCursor(matched)


class FakeValidations:
    def __init__(self, reports: list[dict[str, Any]]) -> None:
        self._reports = reports

    async def find_one(
        self,
        query: dict[str, object],
        *,
        sort: list[tuple[str, int]],
    ) -> dict[str, Any] | None:
        matched = [
            report
            for report in self._reports
            if all(report.get(key) == value for key, value in query.items())
        ]
        if not matched:
            return None
        key, direction = sort[0]
        return sorted(matched, key=lambda report: report[key], reverse=direction < 0)[0]


def _document(
    share_class: str,
    *,
    value: str = "0,006",
    quoted_per_shares: str = "1000",
    percentage: str = "0,070588",
    event_type: str = "DIVIDENDO",
    last_date_prior: str = "15/04/2024",
) -> dict[str, Any]:
    return {
        "source": "b3",
        "cvm_code": "9512",
        "module": "CASH_DIVIDEND_B3",
        "payload": {
            "share_class": share_class,
            "event_type": event_type,
            "value": value,
            "quoted_per_shares": quoted_per_shares,
            "percentage_of_price": percentage,
            "last_date_prior": last_date_prior,
            "approval_date": "01/03/2024",
        },
    }


async def test_reader_normalizes_the_b3_lot_and_preserves_event_dates() -> None:
    collection = FakeCollection([_document("PN")])
    reader = MongoCashEventReader(collection, registrant_resolver=lambda ticker: "9512")

    events = await reader.cash_events("PETR4")

    assert collection.queries == [
        {"source": "b3", "cvm_code": "9512", "module": "CASH_DIVIDEND_B3"}
    ]
    assert len(events) == 1
    # B3's historical BBDC row: R$ 0.006 per lot of 1,000 shares.
    assert events[0].amount_per_share == Decimal("0.000006")
    assert events[0].percentage == Decimal("0.070588")
    assert events[0].last_with_right == date(2024, 4, 15)
    assert events[0].effective == date(2024, 4, 16)
    assert events[0].approval_date == date(2024, 3, 1)


async def test_explicit_class_reads_a_unit_component_and_deduplicates_mirror_runs() -> (
    None
):
    pna = _document("PNA", value="120,00", quoted_per_shares="1.000")
    collection = FakeCollection([pna, pna, _document("PNB")])
    reader = MongoCashEventReader(collection, registrant_resolver=lambda ticker: "9512")

    events = await reader.cash_events(
        "TAEE11", per_share_class=PerShareClass.PREFERRED_A
    )

    assert len(events) == 1
    assert events[0].amount_per_share == Decimal("0.12")


async def test_same_economic_fields_with_different_quotation_scales_stay_distinct() -> (
    None
):
    per_share = _document("PN", value="0,006", quoted_per_shares="1")
    per_lot = _document("PN", value="0,006", quoted_per_shares="1000")
    reader = MongoCashEventReader(
        FakeCollection([per_share, per_lot]),
        registrant_resolver=lambda ticker: "9512",
    )

    events = await reader.cash_events("PETR4")

    assert len(events) == 2
    assert {event.amount_per_share for event in events} == {
        Decimal("0.006"),
        Decimal("0.000006"),
    }


async def test_same_date_dividend_and_interest_remain_distinct() -> None:
    dividend = _document("PN", event_type="DIVIDENDO")
    interest = _document("PN", event_type="JRS CAP PROPRIO")
    reader = MongoCashEventReader(
        FakeCollection([dividend, interest]),
        registrant_resolver=lambda ticker: "9512",
    )

    events = await reader.cash_events("PETR4")

    assert len(events) == 2


async def test_unreadable_absolute_value_is_retained_for_named_dy_null() -> None:
    collection = FakeCollection([_document("ON", value="--")])
    reader = MongoCashEventReader(collection, registrant_resolver=lambda ticker: "9512")

    events = await reader.cash_events("PETR3")

    assert len(events) == 1
    assert events[0].amount_per_share is None
    assert events[0].percentage == Decimal("0.070588")


async def test_absent_mirror_is_not_silently_read_as_zero_distributions() -> None:
    reader = MongoCashEventReader(
        FakeCollection([]), registrant_resolver=lambda ticker: "9512"
    )

    assert await reader.cash_events("PETR4") is None


async def test_an_accepted_zero_row_batch_is_an_economic_zero() -> None:
    reader = MongoCashEventReader(
        FakeCollection([]),
        registrant_resolver=lambda ticker: "9512",
        validation_collection=FakeValidations(
            [
                {
                    "source": "b3",
                    "module": "CASH_DIVIDEND_B3",
                    "batch": "GetListedCashDividends:RDNI",
                    "status": "accepted",
                    "recorded_at": 1,
                    "observations": {
                        "coverage_established": True,
                        "rows": 0,
                    },
                }
            ]
        ),
    )

    assert await reader.cash_events("RDNI3") == ()


async def test_the_latest_quarantine_overrides_an_older_confirmed_zero() -> None:
    common = {
        "source": "b3",
        "module": "CASH_DIVIDEND_B3",
        "batch": "GetListedCashDividends:RDNI",
    }
    reader = MongoCashEventReader(
        FakeCollection([]),
        registrant_resolver=lambda ticker: "9512",
        validation_collection=FakeValidations(
            [
                {
                    **common,
                    "status": "accepted",
                    "recorded_at": 1,
                    "observations": {"coverage_established": True, "rows": 0},
                },
                {
                    **common,
                    "status": "quarantined",
                    "recorded_at": 2,
                    "observations": {"coverage_established": False, "rows": 0},
                },
            ]
        ),
    )

    assert await reader.cash_events("RDNI3") is None


async def test_latest_partial_batch_quarantines_an_existing_partial_mirror() -> None:
    """A source rejection must not expose rows admitted by an earlier run."""
    reader = MongoCashEventReader(
        FakeCollection([_document("PN")]),
        registrant_resolver=lambda ticker: "9512",
        validation_collection=FakeValidations(
            [
                {
                    "source": "b3",
                    "module": "CASH_DIVIDEND_B3",
                    "batch": "GetListedCashDividends:PETR",
                    "status": "quarantined",
                    "recorded_at": 2,
                    "observations": {
                        "rows": 2,
                        "fetched": 2,
                        "accepted": 1,
                        "rejected": 1,
                        "deduplicated": 0,
                        "coverage_established": False,
                    },
                    "evidence": {
                        "rejected_rows": [{"finding": {"code": "row-reconciliation"}}]
                    },
                }
            ]
        ),
    )

    assert await reader.cash_events("PETR4") is None
