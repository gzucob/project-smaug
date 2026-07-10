"""MongoSharesReader: per-year share count from the CAPITAL raw mirror."""

from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

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
    fetched_at: datetime | None = None,
) -> dict[str, Any]:
    return {
        "ticker": ticker,
        "source": "cvm",
        "module": "CAPITAL",
        "fetched_at": fetched_at or datetime(2026, 1, 1, tzinfo=UTC),
        "payload": {
            "reference_date": f"{year}-12-31",
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
