# project-smaug

Personal stock portfolio analysis tool. Phase 1: faithful ingestion of
fundamental data (CVM/B3) into MongoDB (raw mirror, no calculation). Phase 2:
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
- Price source for Phase 2 — B3's own published series
  (`COTAHIST_A{year}.ZIP`, free and unauthenticated, ADR 0032). B3 publishes the
  price **as traded** while the share counts are restated onto the current base
  (ADR 0027); the two must sit on the same base or every company that ever split
  is mispriced (BBAS3 by 2×), so the reader is wrapped in
  `RestatedPriceProvider`, which divides each **session** by the actions that
  postdate it (ADR 0033). **The ratio and the date come from different places.**
  The ratio is always anchored on a filed share count: CVM declares it with the
  counts on both sides (`CAPITAL_EVENT`) but stops after the 2023 FRE, and the
  inference off two filed counts is the fallback. The date comes from B3 — the
  event feed where it exists (`CAPITAL_EVENT_B3`, ADR 0034), and otherwise from
  COTAHIST itself, which numbers each paper's rights state (`DISMES`) and marks
  the ex session in `ESPECI` (ADR 0035). The tape is the complete one: over 60
  companies it names all 47 actions B3's feed lists, plus 43 the feed omits.
  **B3 is the only price source and CVM the only filing source** — the vendor
  chain (Yahoo/brapi) was deleted in ADR 0041, along with the last credential:
  every source is public and unauthenticated. There is no fallback, by design:
  an absent price reads as a null with a named cause instead of as somebody
  else's number on another basis. Because B3 files each year under the code that
  traded then, **a year is read under every code the security has traded as**
  (ADR 0042): the FCA names the codes one registrant filed for a share class, and
  two of them join only where the price crosses the seam between them — which is
  what keeps a merger's extinguished side (ALLL3, BRML3, LAME4) out of the
  survivor's series — **or where the restatement has dated the action that seam
  carries** (ADR 0043: B3 restarts a renamed code's rights state, so the seam is
  the only witness to an action executed on it). A code retired before the FCA
  began naming codes (2018) is recovered from the tape instead — one code stops,
  the next starts at the same price — and confirmed against the names CVM filed
  from 2010 on (ADR 0044); neither witness is trusted alone. A year the named
  codes cannot cover is a null with a cause, never a partial average.
  **Three price bases
  exist and must never be
  mixed**: as traded · adjusted for splits/groupings/bonuses (what indicators
  use) · adjusted for dividends (total return only, ADR 0018).
- The cap is derived, not fetched: it sums the company's listed share classes,
  each at its own price (`Σ class_price × class_shares`, ADR 0014), counting only
  the shares actually **outstanding** — issued less treasury (ADR 0017).

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
- Don't add a credentialed source. Every source today is public and
  unauthenticated (ADR 0041), so nothing in `.env` is a secret — keep it that
  way; the repo is public.
- Don't write business logic in entrypoints (CLI/API) — they call use cases.
- Don't put calculation/indicator logic in the `ingestion` context — that's
  `analysis`'s job (ingestion stays a raw mirror, with no interpretation).
- Don't turn the API (`entrypoints/api.py`) into a write surface —
  calculation and persistence remain exclusive to the `analyze` command (CLI).
- Code, commits, and PRs in English.
