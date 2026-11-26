"""Run the Python suite with isolated service containers and optional coverage."""

import argparse
import subprocess
from pathlib import Path
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[2]


def main() -> int:
    """Run Python checks and reconcile this invocation's Compose resources."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--coverage", action="store_true")
    args = parser.parse_args()
    compose = [
        "docker",
        "compose",
        "--project-name",
        f"smaug-quality-{uuid4().hex}",
        "-f",
        str(ROOT / "scripts/quality/compose.yaml"),
    ]
    checks = [
        "uv sync --frozen --no-editable",
        "uv run --no-sync ruff check --no-cache .",
        "uv run --no-sync ruff format --check --no-cache .",
        "uv run --no-sync mypy --cache-dir=/tmp/mypy src",
    ]
    pytest = "uv run --no-sync pytest -p no:cacheprovider"
    if args.coverage:
        checks.extend(
            [
                f"{pytest} --cov=src/smaug --cov=scripts --cov=alembic --cov-report=",
                "uv run --no-sync python scripts/quality/python_coverage.py",
            ]
        )
    else:
        checks.append(pytest)
    status = 0
    try:
        subprocess.run(
            [*compose, "up", "-d", "--wait", "mongo", "postgres"],
            cwd=ROOT,
            check=True,
        )
        subprocess.run(
            [
                *compose,
                "run",
                "--rm",
                "--no-deps",
                "python",
                "sh",
                "-ec",
                "\n".join(checks),
            ],
            cwd=ROOT,
            check=True,
        )
    except subprocess.CalledProcessError as error:
        status = error.returncode
    finally:
        cleanup = subprocess.run(
            [*compose, "down", "--volumes", "--remove-orphans"],
            cwd=ROOT,
            check=False,
        )
    return status or cleanup.returncode


if __name__ == "__main__":
    raise SystemExit(main())
