"""Check the executable lines in each Python source file."""

from pathlib import Path

from coverage import Coverage

ROOT = Path(__file__).resolve().parents[2]
SOURCE_DIRECTORIES = ("src/smaug", "scripts", "alembic")


def main() -> int:
    """Print every file below the unrounded per-file threshold."""
    coverage = Coverage()
    coverage.load()
    failed = False
    for directory in SOURCE_DIRECTORIES:
        for path in sorted((ROOT / directory).rglob("*.py")):
            _, executable, _, missing, _ = coverage.analysis2(str(path))
            if not executable:
                continue
            total = len(executable)
            covered = total - len(missing)
            if covered * 10 < total * 9:
                failed = True
                print(
                    f"{path.relative_to(ROOT).as_posix()}: "
                    f"{covered}/{total} executable lines ({covered / total:.2%})"
                )
    return int(failed)


if __name__ == "__main__":
    raise SystemExit(main())
