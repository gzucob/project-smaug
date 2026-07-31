"""Relinking the mirror onto the registrant key (ADR 0030).

The documents were collected under a ticker and already know their company; the
relink only writes down what the ticker already implies. So the properties that
matter are that it is idempotent, that it never restates a registrant already on
file, and that a ticker it cannot resolve is reported rather than guessed.
"""

from __future__ import annotations

from dataclasses import replace

from smaug.ingestion.application.relink import RelinkMirrorUseCase
from tests.fakes import FakeRawIngestionRepository, make_snapshot

_PETROBRAS = "9512"
_VALE = "4170"


async def _mirror(*tickers: str) -> FakeRawIngestionRepository:
    repository = FakeRawIngestionRepository()
    for ticker in tickers:
        for module in ("BPA", "DRE"):
            await repository.add(
                replace(make_snapshot(ticker, module, {}), source="cvm")
            )
    return repository


def _resolver(mapping: dict[str, str]):  # noqa: ANN202 - a test-local closure
    def resolve(ticker: str) -> str | None:
        return mapping.get(ticker)

    return resolve


async def test_every_document_of_a_ticker_gains_its_registrant() -> None:
    repository = await _mirror("PETR4")

    report = await RelinkMirrorUseCase(
        repository, registrant_resolver=_resolver({"PETR4": _PETROBRAS})
    ).execute()

    assert report.documents == 2
    assert report.linked == {"PETR4": 2}
    assert {item.cvm_code for item in repository.items} == {_PETROBRAS}


async def test_a_second_run_changes_nothing() -> None:
    """It fills a gap; once filled there is no gap, whatever else has happened."""
    repository = await _mirror("PETR4", "VALE3")
    resolver = _resolver({"PETR4": _PETROBRAS, "VALE3": _VALE})
    use_case = RelinkMirrorUseCase(repository, registrant_resolver=resolver)

    first = await use_case.execute()
    second = await use_case.execute()

    assert first.documents == 4
    assert second.documents == 0
    assert second.linked == {}


async def test_a_ticker_nothing_resolves_is_reported_not_guessed() -> None:
    repository = await _mirror("PETR4", "NOPE99")

    report = await RelinkMirrorUseCase(
        repository, registrant_resolver=_resolver({"PETR4": _PETROBRAS})
    ).execute()

    assert report.unresolved == ("NOPE99",)
    assert report.linked == {"PETR4": 2}
    # Left on the ticker key, still readable — a gap, not a loss.
    unnamed = [i for i in repository.items if i.cvm_code is None]
    assert {i.ticker for i in unnamed} == {"NOPE99"}
