---
description: Test layout, naming-convention drift, fakes, and battery selection
applies_to: tests/**/*.py
---

# Testing Rules

## Layout

Tests are **flat** under `tests/` — there is no `tests/unit/` vs
`tests/integration/` split, and no `conftest.py`. Shared test doubles
(`Protocol` implementations for `FundamentalsReader`, `PriceProvider`,
`AnalysisRepository`, etc.) live in `tests/fakes.py` and are imported directly
by the tests that need them. Sample payloads (e.g. a real CVM quarterly
response) live under `tests/fixtures/`.

`pytest-asyncio` runs in `asyncio_mode = "auto"` (`pyproject.toml`) — async
test functions don't need `@pytest.mark.asyncio`.

## Naming convention — two styles coexist

Older test files (`test_brapi_client.py`, `test_ingest_use_case.py`,
`test_portfolio.py`, `test_event_bus.py`, `test_completeness_report.py`,
`test_repository_mapping.py`, `test_cli_format.py`, `test_smoke.py`) use
`test_should_X_when_Y`. Newer files (`test_analyze.py`, `test_calculator.py`,
`test_ttm.py`, `test_brapi_price.py`, `test_cvm_source.py`,
`test_mongo_fundamentals.py`) dropped the `should_`/`when_` scaffolding for a
plain descriptive name, e.g.
`test_ttm_sums_isolated_flows_and_takes_latest_stocks`.

This is real, unresolved drift, not a rule to enforce either way. When adding
a test to an existing file, match that file's convention. When starting a new
test file, the plain descriptive style is what recent work has converged on.

## No coverage threshold

CI (`uv run pytest`) and `pyproject.toml` don't set a `--cov-fail-under`.
Don't assume a percentage-based coverage gate exists — if coverage becomes a
concern, raise it with the user rather than encoding a threshold here
preemptively.

## Battery Selection

| Change type | Run |
|---|---|
| Single domain/application module | `uv run pytest tests/test_<module>.py` |
| Touches a repository/port implementation (Mongo or Postgres) | Also run the tests exercising that port's fake in `tests/fakes.py`, plus the mapping test (`test_repository_mapping.py` for Mongo, `test_mongo_fundamentals.py` for the CVM→financials bridge) |
| CLI output formatting | `test_cli_format.py` |
| Anything touching `analysis` or `ingestion` end-to-end | Full `uv run pytest` — the suite is small enough that a full run is the default, not an escalation |

## Pre-Commit Gate

See `.claude/RULES/RULES_GIT_WORKFLOW.md` for the full gate (ruff, mypy,
pytest) — this file only adds test-selection guidance on top of it.
