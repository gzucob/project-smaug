"""The whole-exchange resume guard: what each company is still owed (#178)."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, datetime
from pathlib import Path

from smaug.entrypoints.cli import _by_owed_modules, _work_plan
from smaug.ingestion.domain.entities import RawIngestion
from smaug.ingestion.domain.ports import RawFetchResult
from smaug.ingestion.domain.runs import ParserIdentity
from smaug.ingestion.infrastructure.routed_source import RoutedDataSource
from smaug.shared.artifacts import SourceArtifact
from tests.fakes import FakeRawIngestionRepository

STATEMENTS = "dfp_cia_aberta_2024.zip"
FRE = "fre_cia_aberta_2024.zip"
STATEMENTS_ID = "sha256:" + "1" * 64
FRE_ID = "sha256:" + "2" * 64
MODULES = ("DRE", "CAPITAL", "CASH_DIVIDEND_B3")
CODES = {"PETR4": "9512", "VALE3": "4170"}


class _ArchiveSource:
    """A source that reads one yearly CVM archive."""

    def __init__(self, archive: str, artifact_id: str) -> None:
        self.archive_name = archive
        self._artifact = SourceArtifact(
            artifact_id,
            artifact_id.removeprefix("sha256:"),
            1,
            Path(archive),
        )
        self.parser_identity = ParserIdentity("test.archive", 1)

    async def artifact(self) -> SourceArtifact:
        return self._artifact

    async def fetch(self, ticker: str, module: str) -> Sequence[RawFetchResult]:
        return []


class _ExchangeSource:
    """A source with no archive behind it — B3 answers the whole history at once."""

    parser_identity = ParserIdentity("test.exchange", 1)

    async def fetch(self, ticker: str, module: str) -> Sequence[RawFetchResult]:
        return []


def _source() -> RoutedDataSource:
    return RoutedDataSource(
        {
            "CAPITAL": _ArchiveSource(FRE, FRE_ID),
            "CASH_DIVIDEND_B3": _ExchangeSource(),
        },
        default=_ArchiveSource(STATEMENTS, STATEMENTS_ID),
    )


def _mirrored(
    module: str, code: str, *, file: str | None, artifact_id: str | None = None
) -> RawIngestion:
    return RawIngestion(
        ticker="",
        source="cvm",
        module=module,
        fetched_at=datetime(2026, 8, 2, tzinfo=UTC),
        request={"file": file} if file is not None else {"source": "b3"},
        http_status=200,
        payload={},
        cvm_code=code,
        artifact_id=artifact_id,
    )


async def _plan(
    repository: FakeRawIngestionRepository, tickers: tuple[str, ...] = ("PETR4",)
) -> dict[str, tuple[str, ...]]:
    return await _work_plan(repository, _source(), tickers, CODES, MODULES)


async def test_a_new_module_is_owed_by_an_already_mirrored_company() -> None:
    repository = FakeRawIngestionRepository()
    repository.items.append(
        _mirrored("DRE", "9512", file=STATEMENTS, artifact_id=STATEMENTS_ID)
    )
    repository.items.append(_mirrored("CAPITAL", "9512", file=FRE, artifact_id=FRE_ID))

    assert await _plan(repository) == {"PETR4": ("CASH_DIVIDEND_B3",)}


async def test_a_company_owing_nothing_drops_out_of_the_plan() -> None:
    repository = FakeRawIngestionRepository()
    repository.items.append(
        _mirrored("DRE", "9512", file=STATEMENTS, artifact_id=STATEMENTS_ID)
    )
    repository.items.append(_mirrored("CAPITAL", "9512", file=FRE, artifact_id=FRE_ID))
    repository.items.append(_mirrored("CASH_DIVIDEND_B3", "9512", file=None))

    assert await _plan(repository) == {}


async def test_an_archive_module_is_owed_again_for_another_year() -> None:
    repository = FakeRawIngestionRepository()
    repository.items.append(
        _mirrored(
            "DRE",
            "9512",
            file="dfp_cia_aberta_2023.zip",
            artifact_id="sha256:" + "3" * 64,
        )
    )
    repository.items.append(
        _mirrored(
            "CAPITAL",
            "9512",
            file="fre_2023.zip",
            artifact_id="sha256:" + "4" * 64,
        )
    )

    assert await _plan(repository) == {"PETR4": ("DRE", "CAPITAL", "CASH_DIVIDEND_B3")}


async def test_same_filename_with_changed_content_is_owed_again() -> None:
    repository = FakeRawIngestionRepository()
    repository.items.append(
        _mirrored(
            "DRE",
            "9512",
            file=STATEMENTS,
            artifact_id="sha256:" + "9" * 64,
        )
    )

    assert await _plan(repository) == {"PETR4": ("DRE", "CAPITAL", "CASH_DIVIDEND_B3")}


async def test_legacy_filename_without_identity_is_recollected_once() -> None:
    repository = FakeRawIngestionRepository()
    repository.items.append(_mirrored("DRE", "9512", file=STATEMENTS))

    assert await _plan(repository) == {"PETR4": ("DRE", "CAPITAL", "CASH_DIVIDEND_B3")}


async def test_an_exchange_module_is_owed_once_and_never_per_archive() -> None:
    # B3's endpoint returns the whole history in one call, so a document filed
    # under no archive still marks the module done for the next year's pass.
    repository = FakeRawIngestionRepository()
    repository.items.append(_mirrored("CASH_DIVIDEND_B3", "9512", file=None))

    assert await _plan(repository) == {"PETR4": ("DRE", "CAPITAL")}


async def test_an_unmapped_ticker_is_owed_everything() -> None:
    repository = FakeRawIngestionRepository()
    repository.items.append(
        _mirrored("DRE", "9512", file=STATEMENTS, artifact_id=STATEMENTS_ID)
    )

    plan = await _work_plan(repository, _source(), ("BOOM3",), CODES, MODULES)

    assert plan == {"BOOM3": MODULES}


async def test_a_run_with_nothing_mirrored_owes_every_module_to_everyone() -> None:
    repository = FakeRawIngestionRepository()

    assert await _plan(repository, ("PETR4", "VALE3")) == {
        "PETR4": MODULES,
        "VALE3": MODULES,
    }


def test_grouping_splits_a_resumed_run_by_the_modules_it_owes() -> None:
    groups = _by_owed_modules(
        {
            "PETR4": ("CASH_DIVIDEND_B3",),
            "VALE3": MODULES,
            "BBAS3": ("CASH_DIVIDEND_B3",),
        }
    )

    assert groups == [
        (("CASH_DIVIDEND_B3",), ("PETR4", "BBAS3")),
        (MODULES, ("VALE3",)),
    ]


def test_a_fresh_sweep_is_a_single_pass() -> None:
    assert _by_owed_modules(dict.fromkeys(("PETR4", "VALE3"), MODULES)) == [
        (MODULES, ("PETR4", "VALE3"))
    ]
