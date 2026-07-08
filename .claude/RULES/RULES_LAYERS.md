---
description: Bounded contexts, DDD-lite layering, and cross-context communication
applies_to: src/smaug/**/*.py
---

# Architecture and Layering Rules

## DDD Lite — isolated contexts, not a monolith of layers

`src/smaug/` is organized as isolated bounded contexts. Each context owns its
own `domain/`, `application/`, and `infrastructure/` — contexts never import
each other's internals directly.

## Bounded Contexts

| Context | Responsibility |
|---|---|
| `ingestion` | Faithful, uninterpreted mirror of brapi/CVM raw data into MongoDB (Phase 1) |
| `analysis` | Derives standardized financials, computes indicators, persists to PostgreSQL (Phase 2) |
| `portfolio` | Static ticker → sector / CVM-code mapping — pure lookup, no persistence |
| `shared` | Config, DB connections (Mongo + Postgres), EventBus, logging, typed errors |
| `entrypoints` | CLI (`typer`) and the read-only FastAPI app — composition roots, no business logic |

Note: `CLAUDE.md`'s project description still frames "Fase 2" as future scope
that "não vive aqui ainda" — the `analysis` context above is already
implemented (see git history: PRs #3–#15). Treat the code as the source of
truth over that framing; flag it to the user if it needs updating.

## Layer Hierarchy

```
domain/          ← pure logic: frozen entities, Protocol ports, pure calculators
    ↓
application/     ← use cases: orchestrate ports, no I/O of their own
    ↓
infrastructure/  ← Beanie/Mongo, SQLAlchemy/Postgres, httpx clients, CVM ZIP parsing
    ↓
entrypoints/     ← CLI commands and FastAPI routes — wire concrete infra to use cases
```

### Rules

- **Domain never imports infrastructure.** No `beanie`, no `sqlalchemy`, no
  `httpx` in `domain/`.
- **Application depends only on domain ports** (`Protocol` classes in
  `domain/ports.py` or `domain/repositories.py`), never on a concrete infra
  class.
- **Entrypoints hold no business logic.** `cli.py` and `api.py` build the
  concrete repository/port implementations and call a use case
  (`AnalyzePortfolioUseCase`, the ingestion equivalent) — they don't compute
  or transform data themselves.
- **Infrastructure models never leak.** A Beanie `Document` or a SQLAlchemy
  row never crosses into `application/` or `entrypoints/` — conversion
  (`_to_entity` / `_to_document` / `_to_row`) stays inside the infra file.

## Cross-Context Communication

`src/smaug/shared/events.py` defines an in-process, synchronous `EventBus`
(`DomainEvent` base + `subscribe`/`publish`). As of this writing it has **no
subscribers** — it exists so `analysis` (or a future context) can react to
ingestion events without `ingestion` ever importing `analysis`. Don't add a
direct cross-context import as a shortcut; publish/subscribe through the bus,
or thread the data through the entrypoint composition root instead.

## Two names for the same concept: `ports.py` vs `repositories.py`

`ingestion/domain/repositories.py` and `analysis/domain/ports.py` both define
`Protocol` interfaces for the same purpose (the use case's dependency
boundary) — this isn't drift to fix, it reflects what each context actually
depends on: `repositories.py` when the only external dependency is storage;
`ports.py` when the boundary also covers a non-storage service (here,
`PriceProvider`, an HTTP client — not a repository). Name a new context's
interface file after what's actually in it.

## No multi-tenancy

This is a single-portfolio personal tool — there is no `company_id` or
tenant-scoping concern anywhere in the codebase. Do not add one speculatively.
