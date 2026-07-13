# project-smaug

Personal stock portfolio analysis tool. Phase 1: faithful ingestion of
fundamental data (brapi/CVM) into MongoDB (raw mirror, no calculation). Phase 2:
analysis — fundamental + market indicators derived and persisted in
PostgreSQL, served by a read API. Both phases are already implemented (see
`src/smaug/analysis/` and the PR history).

## Stack
- Python 3.13 · uv · mypy strict · ruff · pytest
- Phase 1 (ingestion, raw mirror): MongoDB (Docker) + Beanie. Trigger is CLI
  (`smaug.ingest`, `smaug.report`), not the API.
- Phase 2 (analysis, derived data): PostgreSQL + SQLAlchemy + Alembic
  (migrations in `alembic/versions/`). The calculation trigger is CLI
  (`smaug.analyze`); FastAPI (`smaug.entrypoints.api`) serves already-persisted
  results — it's a read API, not a write one.
- Price source for Phase 2 (current quote + dividend-adjusted year history):
  **Yahoo Finance is primary, brapi is the fallback** (ADR 0013). The cap is
  derived, not fetched: the cap sums the company's listed share classes, each at
  its own price (`Σ class_price × class_shares`, ADR 0014). brapi is also an alternative
  ingestion source (`INGESTION_SOURCE=brapi`), limited on the free plan.

Always restate the stack before proposing architecture or dependencies.

## Source of Truth
- The code is the source of truth for implemented behavior.
- `docs/ROADMAP.md` — the objective, broken into milestones M0–M3.
- `docs/adr/` — why each modelling/architecture choice was made. Immutable.
- GitHub issues — what is left. A follow-up lives here, never in prose.
- `.claude/RULES/` — durable engineering rules; `RULES_DOCS.md` says which
  artifact a given fact belongs in.

What is true about the *data* right now is never a document — it comes from a
command (`smaug doctor`) and from the tests. `docs/PLANO_FASE1.md`,
`docs/preview_fase1_criterios_implementacao.md` and `docs/FINDINGS_INDICATORS.md`
were retired in #43; their decisions are ADRs 0001–0006, their follow-ups are
issues, and the files remain in git history.

## Rules Index
| File | Covers |
|---|---|
| `.claude/RULES/RULES_BRANCHES.md` | Branching, squash-merge, workflow from main |
| `.claude/RULES/RULES_ISSUES.md` | `[NAMESPACE-NN]` format, area/priority/type labels |
| `.claude/RULES/RULES_DOCS.md` | Artifact model: rules vs ADR vs issue vs generated report |
| `.claude/RULES/RULES_GIT_WORKFLOW.md` | Quality gate, commit, push, PR |
| `.claude/RULES/RULES_LAYERS.md` | Bounded contexts, domain→application→infra→entrypoints hierarchy, EventBus |
| `.claude/RULES/RULES_ENTITIES.md` | Frozen entities, Beanie/SQLAlchemy models, API DTOs |
| `.claude/RULES/RULES_REPOSITORIES.md` | Protocol pattern for ports/repositories, infra conversion |
| `.claude/RULES/RULES_TYPING.md` | mypy strict, `X \| None`, docstring style, Ruff |
| `.claude/RULES/RULES_TESTING.md` | Test layout, naming convention, battery selection |
| `.claude/RULES/RULES_FRONTEND.md` | Next.js front-end: stack, "Smaug" design system, data boundary, dev workflow |

## Architecture (DDD Lite)
Isolated contexts under `src/smaug/`: `ingestion`, `analysis`, `portfolio`,
`shared`, `entrypoints`. Layers: domain → application → infrastructure →
entrypoints. Cross-context communication only via events (in-process EventBus).
Details in `.claude/RULES/RULES_LAYERS.md`.

## What NOT to Do
- Don't push directly to `main` — always branch + PR + squash.
- Don't commit secrets — the brapi token only lives in `.env` (gitignored). The repo is public.
- Don't write business logic in entrypoints (CLI/API) — they call use cases.
- Don't put calculation/indicator logic in the `ingestion` context — that's
  `analysis`'s job (ingestion stays a raw mirror, with no interpretation).
- Don't turn the API (`entrypoints/api.py`) into a write surface —
  calculation and persistence remain exclusive to the `analyze` command (CLI).
- Code, commits, and PRs in English.
