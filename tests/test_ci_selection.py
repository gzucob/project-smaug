"""CI selection preserves module boundaries and the release battery."""

import json

import pytest
from scripts.quality.ci_selection import JOBS, select, verify

PACKAGE_JOBS = {"installer", "desktop-packages", "manager-source"}
CONTRACT_JOBS = {"quality", "frontend", "installer"}


@pytest.mark.parametrize(
    ("paths", "jobs", "browser"),
    [
        (["README.md", ".github/AGENTS.md", "docs/guia técnico.md"], set(), False),
        (["docs/installation.md"], {"generated-docs"}, False),
        (["docs/mcp.md"], {"generated-docs"}, False),
        (
            ["src/smaug/shared/setup_guide.md"],
            {"generated-docs"} | PACKAGE_JOBS,
            False,
        ),
        (["src/smaug/ingestion/domain/repositories.py"], {"quality"}, False),
        (["pyproject.toml", "uv.lock"], {"quality"}, False),
        (["frontend/src/components/TickerCard.tsx"], {"frontend"}, False),
        (["website/src/App.tsx"], {"website"}, False),
        (["installer/gui_test.go"], {"manager", "manager-source"}, False),
        (["installer/compose.yaml", "installer/images.json"], PACKAGE_JOBS, False),
        (
            ["installer/install.ps1"],
            {"manager"} | PACKAGE_JOBS,
            False,
        ),
        (["scripts/installer/package_desktop.py"], PACKAGE_JOBS, False),
        (["scripts/distribution_snapshot.py"], PACKAGE_JOBS, False),
        (["src/smaug/entrypoints/api.py"], CONTRACT_JOBS, True),
        (["src/smaug/analysis/domain/indicators.py"], CONTRACT_JOBS, True),
        (["frontend/src/lib/types.ts"], CONTRACT_JOBS, True),
        (["frontend/src/app/api/portfolio/[ticker]/route.ts"], CONTRACT_JOBS, True),
        (["frontend/Dockerfile"], {"frontend"} | PACKAGE_JOBS, False),
        (["Dockerfile"], {"quality"} | PACKAGE_JOBS, False),
        (["docker-compose.yml"], {"quality", "frontend"}, False),
        ([".github/workflows/promotion.yml"], {"workflows"}, False),
        (
            [".github/workflows/release.yml"],
            {"workflows", "website"} | PACKAGE_JOBS,
            False,
        ),
        ([".github/workflows/ci.yml"], JOBS, True),
        (
            ["src/smaug/shared/db.py", "frontend/src/lib/format.ts", "README.md"],
            {"quality", "frontend"},
            False,
        ),
    ],
)
def test_selected_jobs_for_develop_changes(
    paths: list[str], jobs: set[str], browser: bool
) -> None:
    selection = select(paths, full=False, promotion=False)

    assert json.loads(selection["required"]) == sorted(jobs)
    assert {job for job in JOBS if selection[job] == "true"} == jobs
    assert (selection["browser"] == "true") is browser
    expected_runners = ["ubuntu-22.04"]
    if "desktop-packages" in jobs:
        expected_runners.append("ubuntu-22.04-arm")
    assert json.loads(selection["installer-runners"]) == expected_runners


def test_release_requires_all_jobs_even_for_documentation() -> None:
    selection = select(["README.md"], full=True, promotion=True)

    assert json.loads(selection["required"]) == sorted(JOBS - {"generated-docs"})
    assert selection["browser"] == "true"
    assert json.loads(selection["installer-runners"]) == [
        "ubuntu-22.04",
        "ubuntu-22.04-arm",
    ]


def test_no_release_promotion_still_validates_workflows() -> None:
    selection = select(["README.md"], full=False, promotion=True)

    assert json.loads(selection["required"]) == ["workflows"]


@pytest.mark.parametrize(
    ("job", "actual"),
    [
        ("frontend", "failure"),
        ("frontend", "skipped"),
        ("frontend", "cancelled"),
        ("quality", "success"),
    ],
)
def test_aggregate_rejects_incorrect_job_results(job: str, actual: str) -> None:
    required = ["frontend"]
    results = dict.fromkeys(JOBS, "skipped")
    results.update(changes="success", frontend="success")
    assert verify(required, results) == []

    results[job] = actual

    assert len(verify(required, results)) == 1


def test_aggregate_rejects_a_failed_classifier() -> None:
    results = dict.fromkeys(JOBS, "skipped")
    results["changes"] = "failure"

    assert verify([], results) == ["Change detection failed"]
