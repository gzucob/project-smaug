"""RoutedDataSource: sends CAPITAL to the FRE source, statements to the default."""

from collections.abc import Sequence

from smaug.ingestion.domain.ports import RawFetchResult
from smaug.ingestion.infrastructure.routed_source import RoutedDataSource


class FakeSource:
    def __init__(self, name: str) -> None:
        self.name = name
        self.calls: list[tuple[str, str]] = []

    async def fetch(self, ticker: str, module: str) -> Sequence[RawFetchResult]:
        self.calls.append((ticker, module))
        return [
            RawFetchResult(
                module=module,
                request={"source": self.name},
                http_status=200,
                payload={},
            )
        ]


async def test_routes_a_known_module_to_its_own_source() -> None:
    capital, statements = FakeSource("fre"), FakeSource("dfp")
    router = RoutedDataSource({"CAPITAL": capital}, default=statements)

    results = await router.fetch("PETR4", "CAPITAL")

    assert results[0].request["source"] == "fre"
    assert capital.calls == [("PETR4", "CAPITAL")]
    assert statements.calls == []


async def test_routes_every_other_module_to_the_default() -> None:
    capital, statements = FakeSource("fre"), FakeSource("dfp")
    router = RoutedDataSource({"CAPITAL": capital}, default=statements)

    results = await router.fetch("PETR4", "DRE")

    assert results[0].request["source"] == "dfp"
    assert capital.calls == []


async def test_module_routing_is_case_insensitive() -> None:
    capital, statements = FakeSource("fre"), FakeSource("dfp")
    router = RoutedDataSource({"CAPITAL": capital}, default=statements)

    await router.fetch("PETR4", "capital")

    assert capital.calls == [("PETR4", "capital")]
