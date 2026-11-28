"""Select CI jobs from the candidate diff and enforce their results."""

import json
import os
import subprocess
import sys

JOBS = frozenset(
    {
        "quality",
        "frontend",
        "website",
        "generated-docs",
        "workflows",
        "manager-source",
        "manager",
        "installer",
        "desktop-packages",
    }
)
CONTRACT_FILES = frozenset(
    {
        "src/smaug/entrypoints/api.py",
        "src/smaug/analysis/domain/formulas.py",
        "src/smaug/analysis/domain/indicators.py",
        "frontend/src/lib/api.ts",
        "frontend/src/lib/api-result.ts",
        "frontend/src/lib/data-revision-api.ts",
        "frontend/src/lib/favorites-api.ts",
        "frontend/src/lib/indicator-docs.ts",
        "frontend/src/lib/types.ts",
        "frontend/tests/installed-browser.mjs",
    }
)
GUIDE_FILES = frozenset(
    {
        "docs/installation.md",
        "docs/mcp.md",
        "scripts/render_setup_guide.py",
        "src/smaug/shared/mcp_setup.py",
        "src/smaug/shared/setup_guide.md",
    }
)


def select(paths: list[str], *, full: bool, promotion: bool) -> dict[str, str]:
    """Return job conditions, the installation matrix, and browser coverage."""
    required: set[str] = set()
    package = full
    browser = full
    if full:
        # The full pytest suite already checks the generated setup guides.
        required.update(JOBS - {"generated-docs"})
    if promotion:
        required.add("workflows")
    for path in paths:
        if path in GUIDE_FILES:
            required.add("generated-docs")
            package |= path.startswith("src/")
        if path.lower().endswith((".md", ".rst", ".adoc")):
            continue

        if path in {".github/workflows/ci.yml", "scripts/quality/ci_selection.py"}:
            required.update(JOBS)
            package = browser = True
        elif path.startswith(".github/workflows/"):
            required.add("workflows")
            if path in {
                ".github/workflows/release.yml",
                ".github/workflows/release-candidate.yml",
                ".github/workflows/prepared-snapshot.yml",
            }:
                package = True
            if path == ".github/workflows/release.yml":
                required.add("website")

        if path in CONTRACT_FILES or path.startswith("frontend/src/app/api/"):
            required.update(("quality", "frontend"))
            browser = True
        if path.startswith(("src/", "tests/", "alembic/")) or path in {
            ".importlinter",
            ".python-version",
            "alembic.ini",
            "pyproject.toml",
            "uv.lock",
            "scripts/ingest_reps.sh",
            "scripts/profile_analysis.py",
            "scripts/quality/compose.yaml",
            "scripts/quality/pre_commit.py",
            "scripts/quality/python_coverage.py",
            "scripts/quality/python_gate.py",
            "scripts/quality/restore_release.sh",
            "scripts/quality/select_promotion.sh",
        }:
            required.add("quality")
        if path.startswith("frontend/"):
            required.add("frontend")
        if path.startswith("website/"):
            required.add("website")

        if path in {"installer/compose.yaml", "installer/images.json"}:
            package = True
        elif path.startswith("installer/") and not path.endswith("/.gitignore"):
            required.add("manager")
            package |= not (
                path.endswith("_test.go")
                or path.startswith(("installer/tests/", "installer/cmd/linecoverage/"))
            )
        if path == "scripts/quality/verify_launchers.py":
            required.add("manager")
        if path.startswith("scripts/installer/") or path in {
            "scripts/distribution_snapshot.py",
            "scripts/quality/stage_release.sh",
            "frontend/Dockerfile",
            "frontend/.dockerignore",
        }:
            package = True
        if path in {"Dockerfile", ".dockerignore", "LICENSE"}:
            required.add("quality")
            package = True
        if path in {"docker-compose.yml", ".env.example"}:
            required.update(("quality", "frontend"))

    if package:
        required.update(("desktop-packages", "installer"))
    if browser:
        required.add("installer")
    if "manager" in required or "desktop-packages" in required:
        required.add("manager-source")
    runners = ["ubuntu-22.04"]
    if package:
        runners.append("ubuntu-22.04-arm")
    return {
        **{job: str(job in required).lower() for job in sorted(JOBS)},
        "required": json.dumps(sorted(required)),
        "installer-runners": json.dumps(runners),
        "browser": str(browser).lower(),
    }


def verify(required: list[str], results: dict[str, str]) -> list[str]:
    """Require success for selected jobs and a skip for every other job."""
    if results.get("changes") != "success":
        return ["Change detection failed"]
    if set(required) - JOBS or set(results) != JOBS | {"changes"}:
        return ["Job results do not match the CI selection"]
    errors = []
    for job in sorted(JOBS):
        expected = "success" if job in required else "skipped"
        if results[job] != expected:
            errors.append(f"{job}: expected {expected}, got {results[job]}")
    return errors


def _changed_paths(base: str, head: str) -> list[str]:
    raw = subprocess.check_output(
        ["git", "diff", "--name-only", "--no-renames", "-z", base, head, "--"]
    )
    return [os.fsdecode(path) for path in raw.split(b"\0") if path]


def main() -> int:
    """Write workflow outputs or validate the completed jobs."""
    if sys.argv[1:] == ["select"]:
        full = os.environ.get("FULL") == "true"
        paths = (
            []
            if full
            else _changed_paths(os.environ["BASE_SHA"], os.environ["GITHUB_SHA"])
        )
        selection = select(paths, full=full, promotion=os.environ["BASE_REF"] == "main")
        with open(os.environ["GITHUB_OUTPUT"], "a", encoding="utf-8") as outputs:
            for key, value in selection.items():
                print(f"{key}={value}", file=outputs)
        return 0
    if sys.argv[1:] == ["verify"]:
        results = {
            job: detail["result"]
            for job, detail in json.loads(os.environ["NEEDS"]).items()
        }
        required = (
            json.loads(os.environ["REQUIRED"])
            if results.get("changes") == "success"
            else []
        )
        errors = verify(required, results)
        for error in errors:
            print(error, file=sys.stderr)
        return int(bool(errors))
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
