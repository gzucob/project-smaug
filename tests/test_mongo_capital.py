"""MongoSharesReader: per-year share counts from the CAPITAL raw mirror."""

from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

from smaug.analysis.domain.financials import ShareCounts
from smaug.analysis.infrastructure.mongo_capital import MongoSharesReader


class FakeCursor:
    def __init__(self, documents: list[dict[str, Any]]) -> None:
        self._documents = documents

    def sort(self, key: str, direction: int) -> "FakeCursor":
        self._documents.sort(key=lambda d: d[key], reverse=direction < 0)
        return self

    async def __aiter__(self) -> Any:
        for document in self._documents:
            yield document


class FakeCollection:
    def __init__(self, documents: list[dict[str, Any]]) -> None:
        self._documents = documents

    def find(self, query: dict[str, Any], /) -> FakeCursor:
        matched = [
            d for d in self._documents if all(d.get(k) == v for k, v in query.items())
        ]
        return FakeCursor(matched)


def _doc(
    ticker: str,
    year: int,
    total: int,
    *,
    common: int | None = None,
    preferred: int = 0,
    fetched_at: datetime | None = None,
) -> dict[str, Any]:
    return {
        "ticker": ticker,
        "source": "cvm",
        "module": "CAPITAL",
        "fetched_at": fetched_at or datetime(2026, 1, 1, tzinfo=UTC),
        "payload": {
            "reference_date": f"{year}-12-31",
            # The FRE writes an absent class as 0, never as a blank.
            "common_shares": total if common is None else common,
            "preferred_shares": preferred,
            "total_shares": total,
        },
    }


async def test_outstanding_returns_the_count_filed_for_that_year() -> None:
    reader = MongoSharesReader(
        FakeCollection([_doc("PETR4", 2024, 13_000), _doc("PETR4", 2025, 12_888)])
    )

    assert await reader.outstanding("PETR4", 2025) == Decimal(12_888)
    assert await reader.outstanding("PETR4", 2024) == Decimal(13_000)


async def test_outstanding_falls_back_to_the_nearest_earlier_year() -> None:
    # 2026 was never ingested; the 2025 filing is the closest thing on file.
    reader = MongoSharesReader(FakeCollection([_doc("PETR4", 2025, 12_888)]))

    assert await reader.outstanding("PETR4", 2026) == Decimal(12_888)


async def test_outstanding_is_none_before_the_earliest_filing() -> None:
    reader = MongoSharesReader(FakeCollection([_doc("PETR4", 2025, 12_888)]))

    assert await reader.outstanding("PETR4", 2021) is None


async def test_outstanding_is_none_for_a_unit_ticker() -> None:
    # A unit quotes a bundle of shares, so the filed count is the wrong divisor.
    reader = MongoSharesReader(FakeCollection([_doc("TAEE11", 2025, 1_033_496_721)]))

    assert await reader.outstanding("TAEE11", 2025) is None


async def test_counts_split_the_filing_by_share_class() -> None:
    reader = MongoSharesReader(
        FakeCollection([_doc("PETR4", 2025, 13_044, common=7_442, preferred=5_602)])
    )

    assert await reader.counts("PETR4", 2025) == ShareCounts(
        common=Decimal(7_442),
        preferred=Decimal(5_602),
        total=Decimal(13_044),
    )


async def test_counts_are_served_for_a_unit_ticker() -> None:
    # The opposite of ``outstanding``: the multi-class cap prices the *underlying*
    # classes, which is exactly what a unit's bundle quote cannot give (ADR 0014).
    reader = MongoSharesReader(
        FakeCollection(
            [_doc("TAEE11", 2025, 1_033_496, common=590_712, preferred=442_784)]
        )
    )

    filed = await reader.counts("TAEE11", 2025)

    assert filed is not None
    assert filed.common == Decimal(590_712)
    assert filed.preferred == Decimal(442_784)


async def test_a_class_filed_as_zero_is_absent_not_zero() -> None:
    # A single-class filer writes 0 preferred shares; zero shares is a gap, never
    # a denominator — the cap must not multiply a price by it.
    reader = MongoSharesReader(
        FakeCollection([_doc("WEGE3", 2025, 4_200, common=4_200, preferred=0)])
    )

    filed = await reader.counts("WEGE3", 2025)

    assert filed is not None
    assert filed.preferred is None


async def test_a_zero_total_is_read_from_the_class_lines_instead() -> None:
    # BBAS3's 2023 FRE files 5.73 bn ordinary shares and then writes 0 in the total
    # column (#39). Dropping the filing over that blank sent 2023 back to 2022's
    # 2.87 bn — half the company, right across the 2:1 bonus. The class line is the
    # filing's own number, so it stands in.
    reader = MongoSharesReader(
        FakeCollection(
            [
                _doc("BBAS3", 2022, 2_865, common=2_865),
                _doc("BBAS3", 2023, 0, common=5_730),
            ]
        )
    )

    assert await reader.outstanding("BBAS3", 2023) == Decimal(5_730)


async def test_a_filing_with_no_usable_count_falls_back_to_the_prior_year() -> None:
    reader = MongoSharesReader(
        FakeCollection(
            [
                _doc("BBAS3", 2022, 2_865, common=2_865),
                _doc("BBAS3", 2023, 0, common=0),
            ]
        )
    )

    assert await reader.outstanding("BBAS3", 2023) == Decimal(2_865)


async def test_outstanding_prefers_the_later_ingestion_of_the_same_year() -> None:
    reader = MongoSharesReader(
        FakeCollection(
            [
                _doc("PETR4", 2025, 111, fetched_at=datetime(2026, 1, 1, tzinfo=UTC)),
                _doc("PETR4", 2025, 222, fetched_at=datetime(2026, 6, 1, tzinfo=UTC)),
            ]
        )
    )

    assert await reader.outstanding("PETR4", 2025) == Decimal(222)


async def test_outstanding_is_none_without_any_capital_document() -> None:
    reader = MongoSharesReader(FakeCollection([]))

    assert await reader.outstanding("PETR4", 2025) is None
