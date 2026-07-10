---
description: mypy strict compliance, typing conventions, docstring style, and Ruff configuration
applies_to: src/smaug/**/*.py
---

# Typing and Code Style Rules

## mypy Strict

`mypy --strict` must pass with zero errors before every commit (see
`.claude/RULES/RULES_GIT_WORKFLOW.md` for the full gate). Run:

```bash
uv run mypy src
```

There are currently **no** `[[tool.mypy.overrides]]` blanket ignores. The last
one (`cvm.*`, for pycvm, which shipped no stubs) was removed with the dependency
in ADR 0009. A new blanket override needs a real justification — a third-party
dependency with no stubs — recorded next to it, not "mypy is being annoying
here."

## Type Annotations

```python
# CORRECT
def find_latest(self, ticker: str, module: str) -> RawIngestion | None: ...

# WRONG — legacy Optional/Union
from typing import Optional, Union
def find_latest(self, ticker: str) -> Optional[RawIngestion]: ...
```

- Always `X | None`, never `Optional[X]`.
- Always `X | Y`, never `Union[X, Y]`.
- `Any` only with an inline justification comment (e.g. the raw CVM payload
  parsing in `mongo_fundamentals.py`, which genuinely reads an untyped dict).

## Docstrings

The codebase does **not** use full Google-style `Args:`/`Returns:`/`Raises:`
blocks. The actual convention (see `calculator.py`, `analyze.py`):

- Module docstring: one line, plus a short paragraph explaining *why* when a
  design choice isn't obvious from the code alone (annualization, sector
  awareness, the two analysis views).
- Public class/function docstring: one line describing what it represents or
  does; add a short paragraph only when the rationale isn't obvious from the
  name and signature (see `_market_for_year`, `_prior_year_annual`).
- Private (`_helper`) functions: usually no docstring when the name is
  self-explanatory.

Follow this style for new code — don't introduce Google-style Args/Returns
blocks; they'd be inconsistent with everything already in the repo.

## Code Style

- Max line length: **88** (Ruff-enforced).
- Ruff rule set: `E, F, I, N, W, UP, B, C4, PT` (see `pyproject.toml`).
- Import order: stdlib → third-party → local (Ruff `I`).
- Prefer pure functions in `domain/` — see `calculator.py` (no I/O,
  deterministic, only `Decimal` arithmetic).
- Composition over inheritance — `Protocol` structural typing, not class
  hierarchies (see `RULES_REPOSITORIES.md`).

## Quality Commands

```bash
uv run ruff check .
uv run ruff format --check .
uv run mypy src
uv run pytest
```

(Duplicated from `RULES_GIT_WORKFLOW.md`'s pre-commit gate intentionally —
that file documents *when* to run this, this file documents *what it checks
and why*.)
