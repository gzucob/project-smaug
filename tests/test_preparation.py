"""Full preparation discovers source coverage and resumes failed work."""

import asyncio
from pathlib import Path

import httpx
import pytest

from smaug.ingestion.domain.preparation import preparation_batches
from smaug.ingestion.infrastructure.archive_catalog import CvmArchiveCatalog
from smaug.shared.config import DEFAULT_CVM_MODULES
from smaug.shared.errors import SourceMalformedError
from smaug.shared.preparation import PreparationStep, prepare_data


async def test_installation_allows_only_b3_quarantine_and_still_requires_verification(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from smaug.entrypoints import cli

    calls: list[dict[str, object]] = []

    async def discover(_catalog: CvmArchiveCatalog) -> dict[str, tuple[int, ...]]:
        return dict.fromkeys(("DFP", "ITR", "FRE"), (2026,))

    async def ingest(*_args: object, **kwargs: object) -> int:
        calls.append(kwargs)
        return 0

    async def analyze(*_args: object, **_kwargs: object) -> int:
        return 0

    async def verify() -> int:
        return 1

    monkeypatch.setattr(CvmArchiveCatalog, "discover", discover)
    monkeypatch.setattr(cli, "_run_ingest", ingest)
    monkeypatch.setattr(cli, "_run_analyze", analyze)
    monkeypatch.setattr(cli, "_verify_prepared_data", verify)
    state = tmp_path / "preparation.json"
    assert await cli._run_prepare(state) == 1
    assert [call["allow_quarantined"] for call in calls] == [False] * 3 + [True]
    assert calls[-1]["modules"] == (
        "LISTING_B3",
        "CAPITAL_EVENT_B3",
        "CASH_DIVIDEND_B3",
    )
    assert '"verification"' not in state.read_text().split('"completed":')[1]


async def test_catalog_uses_published_links_and_keeps_dataset_periods_separate() -> (
    None
):
    def source(request: httpx.Request) -> httpx.Response:
        dataset = request.url.path.split("/")[-3].lower()
        years = (2010, 2025) if dataset != "itr" else (2011, 2026)
        links = "".join(
            f'<a href="{dataset}_cia_aberta_{year}.zip">data</a>' for year in years
        )
        return httpx.Response(
            200,
            text=links
            + '<a href="https://other.example/dfp_cia_aberta_2099.zip">x</a>',
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(source)) as http:
        published = await CvmArchiveCatalog(http).discover()
    batches = preparation_batches(published)
    assert [batch.key for batch in batches] == [
        "DFP-2010",
        "DFP-2025",
        "ITR-2011",
        "ITR-2026",
        "FRE-2010",
        "FRE-2025",
        "B3-2025",
    ]
    assert "CAPITAL" not in batches[0].modules
    assert "DRE" not in batches[-2].modules
    assert "CASH_DIVIDEND_B3" in batches[-1].modules
    assert {module for batch in batches for module in batch.modules} == set(
        DEFAULT_CVM_MODULES
    )


async def test_catalog_rejects_empty_or_changed_directory_instead_of_succeeding() -> (
    None
):
    async with (
        httpx.AsyncClient(
            transport=httpx.MockTransport(
                lambda _: httpx.Response(200, text="unavailable")
            )
        ) as http,
    ):
        with pytest.raises(SourceMalformedError):
            await CvmArchiveCatalog(http).discover()


async def test_preparation_retries_failure_before_analysis_and_resets_on_new_revision(
    tmp_path: Path,
) -> None:
    calls: list[str] = []
    failing = True

    async def ingest() -> int:
        calls.append("ingest")
        return 0

    async def analyze() -> int:
        calls.append("analyze")
        return int(failing)

    async def verify() -> int:
        calls.append("verify")
        return 0

    steps = [
        PreparationStep("ingest", ingest),
        PreparationStep("analyze", analyze),
        PreparationStep("verify", verify),
    ]
    state = tmp_path / "nested" / "preparation.json"
    assert await prepare_data(steps, state_file=state, revision="a") == 1
    assert calls == ["ingest", "analyze"]
    failing = False
    assert await prepare_data(steps, state_file=state, revision="a") == 0
    assert calls == ["ingest", "analyze", "analyze", "verify"]
    calls.clear()
    assert await prepare_data(steps, state_file=state, revision="a") == 0
    assert not calls
    assert await prepare_data(steps, state_file=state, revision="b") == 0
    assert calls == ["ingest", "analyze", "verify"]


async def test_corrupt_checkpoint_rebuilds_from_existing_mirror_and_logs_details(
    tmp_path: Path,
) -> None:
    state = tmp_path / "preparation.json"
    state.write_text("interrupted external edit")
    log = tmp_path / "preparation.log"
    progress: list[str] = []

    async def execute() -> int:
        print("archive and coverage details")
        return 0

    assert (
        await prepare_data(
            [PreparationStep("ingest", execute)],
            state_file=state,
            revision="a",
            operation_log=log,
            progress=progress.append,
        )
        == 0
    )
    assert progress == ["[1/1] ingest"]
    assert log.read_text() == "archive and coverage details\n"


async def test_long_preparation_reports_activity_before_it_finishes(
    tmp_path: Path,
) -> None:
    finished = asyncio.Event()
    progress: list[str] = []

    async def execute() -> int:
        await finished.wait()
        return 0

    def report(message: str) -> None:
        progress.append(message)
        if "em andamento" in message:
            assert not (tmp_path / "state.json").exists()
            finished.set()

    assert (
        await prepare_data(
            [PreparationStep("B3", execute)],
            state_file=tmp_path / "state.json",
            revision="a",
            progress=report,
            progress_interval=0.001,
        )
        == 0
    )
    assert len(progress) >= 2
    assert finished.is_set()
