"""Reading the mirror by registrant instead of by ticker (ADR 0030).

The behaviour worth pinning is the one the old key could not express: ELET3,
ELET5 and ELET6 are one filer, so one mirrored filing must answer for all three.
Under the ticker key that took three identical copies, and three copies are three
chances to disagree about the same fact.
"""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

from smaug.analysis.infrastructure.mirror import mirror_filter, no_registrant
from smaug.analysis.infrastructure.mongo_capital import MongoSharesReader

_ELETROBRAS = "2437"


class _FakeCursor:
    def __init__(self, documents: list[dict[str, Any]]) -> None:
        self._documents = documents

    def sort(self, *_args: Any) -> _FakeCursor:
        return self

    async def __aiter__(self) -> Any:
        for document in self._documents:
            yield document


class _FakeCollection:
    def __init__(self, documents: list[dict[str, Any]]) -> None:
        self._documents = documents

    def find(self, query: dict[str, Any], /) -> _FakeCursor:
        matched = [
            d for d in self._documents if all(d.get(k) == v for k, v in query.items())
        ]
        return _FakeCursor(matched)


def _capital_doc(ticker: str, cvm_code: str | None, total: int) -> dict[str, Any]:
    return {
        "ticker": ticker,
        "cvm_code": cvm_code,
        "source": "cvm",
        "module": "CAPITAL",
        "fetched_at": datetime(2026, 1, 1, tzinfo=UTC),
        "payload": {
            "reference_date": "2024-12-31",
            "version": 1,
            "common_shares": total,
            "preferred_shares": 0,
            "total_shares": total,
            "approval_date": "2024-04-27",
        },
    }


def test_the_filter_names_the_registrant_when_one_resolves() -> None:
    assert mirror_filter("ELET6", lambda _t: _ELETROBRAS, module="CAPITAL") == {
        "source": "cvm",
        "cvm_code": _ELETROBRAS,
        "module": "CAPITAL",
    }


def test_the_filter_falls_back_to_the_ticker_when_none_does() -> None:
    """A brapi document has no registrant, and neither had a CVM one before #109."""
    assert mirror_filter("PETR4", no_registrant) == {
        "source": "cvm",
        "ticker": "PETR4",
    }


async def test_a_sibling_class_reads_the_companys_one_mirrored_filing() -> None:
    # Collected once, under the ON code the batch names the company by.
    collection = _FakeCollection([_capital_doc("ELET3", _ELETROBRAS, 2_000_000_000)])

    reader = MongoSharesReader(collection, registrant_resolver=lambda _t: _ELETROBRAS)

    assert await reader.outstanding("ELET6", 2024) == Decimal(2_000_000_000)


async def test_without_the_registrant_a_sibling_class_finds_nothing() -> None:
    """What the ticker key cost: the same filing, invisible to ELET6."""
    collection = _FakeCollection([_capital_doc("ELET3", _ELETROBRAS, 2_000_000_000)])

    reader = MongoSharesReader(collection)  # no resolver: falls back to the ticker

    assert await reader.outstanding("ELET6", 2024) is None
    assert await reader.outstanding("ELET3", 2024) == Decimal(2_000_000_000)
