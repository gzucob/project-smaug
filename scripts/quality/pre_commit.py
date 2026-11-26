"""Run the staged-source pre-commit quality gate."""

import os
import shutil
import subprocess
import sys
from pathlib import Path
from tempfile import TemporaryDirectory

ROOT = Path(__file__).resolve().parents[2]
INSTALLER = ROOT / "installer"
GO_FILES = (
    '{{range .GoFiles}}{{$.ImportPath}}/{{.}}{{"\t"}}{{$.Dir}}/{{.}}{{println}}{{end}}'
)


def check_go() -> bool:
    """Collect coverage only for Go files applicable to this platform."""
    with TemporaryDirectory(prefix="smaug-go-coverage-") as directory:
        profile = str(Path(directory) / "go.cover")
        tests = subprocess.run(
            ["go", "test", "./...", "-coverpkg=./...", f"-coverprofile={profile}"],
            cwd=INSTALLER,
            check=False,
        )
        if tests.returncode:
            return False
        inventory = subprocess.check_output(
            ["go", "list", "-f", GO_FILES, "./..."], cwd=INSTALLER, text=True
        )
        return (
            subprocess.run(
                ["go", "run", "./cmd/linecoverage", profile],
                input=inventory,
                text=True,
                cwd=INSTALLER,
                check=False,
            ).returncode
            == 0
        )


def staged_sources() -> bool:
    """Keep unstaged source or test changes out of the measurement."""
    paths = ["src", "tests", "scripts", "alembic", "frontend", "website", "installer"]
    diff = subprocess.run(
        ["git", "diff", "--exit-code", "--quiet", "--", *paths],
        cwd=ROOT,
        check=False,
    )
    untracked = subprocess.check_output(
        ["git", "ls-files", "--others", "--exclude-standard", "--", *paths],
        cwd=ROOT,
    )
    if diff.returncode != 0 or untracked:
        print("Stage or set aside unstaged source and test changes before committing.")
        return False
    return True


def main() -> int:
    """Require both successful suites and the per-file coverage floor."""
    npm = shutil.which("npm.cmd" if os.name == "nt" else "npm")
    if npm is None:
        print("Install Node.js 24 with npm to run the pre-commit gate.")
        return 1
    if not staged_sources():
        return 1
    commands = [
        (
            ROOT,
            [sys.executable, "scripts/quality/python_gate.py", "--coverage"],
        ),
        (ROOT / "frontend", [npm, "run", "typecheck"]),
        (ROOT / "frontend", [npm, "run", "build"]),
        (ROOT / "website", [npm, "run", "build"]),
        (ROOT, ["node", "scripts/quality/js_coverage.mjs"]),
        (INSTALLER, ["go", "vet", "./..."]),
        (ROOT, [sys.executable, "scripts/quality/verify_launchers.py"]),
    ]
    failed = False
    for directory, command in commands:
        result = subprocess.run(command, cwd=directory, check=False)
        if result.returncode:
            failed = True
    if not check_go():
        failed = True
    return int(failed)


if __name__ == "__main__":
    raise SystemExit(main())
