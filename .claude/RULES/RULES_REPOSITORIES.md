---
description: Protocol-based port/repository pattern across Mongo and Postgres
applies_to: src/smaug/**/domain/ports.py, src/smaug/**/domain/repositories.py, src/smaug/**/infrastructure/*.py
---

# Repository / Port Pattern Rules

## Interfaces are `Protocol`, not ABC

Every dependency boundary in `domain/` is a `typing.Protocol` — structural
typing, no explicit inheritance required from the implementation:

```python
class FundamentalsReader(Protocol):
    async def history(self, ticker: str) -> list[StandardizedFinancials]: ...
    async def annuals(self, ticker: str) -> list[StandardizedFinancials]: ...
```

Don't switch these to `ABC` — it would force every fake/test double to
subclass explicitly, which the codebase deliberately avoids (`tests/fakes.py`
implements the Protocols structurally, with no inheritance).

## Implementations

Live in `<context>/infrastructure/`, take their storage handle via
constructor injection, and are never instantiated inline inside a use case:

```python
class SqlAlchemyAnalysisRepository:
    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._session_factory = session_factory
```

```python
class BeanieRawIngestionRepository:
    async def add(self, ingestion: RawIngestion) -> RawIngestion: ...
```

Beanie repositories don't take a session parameter — Beanie's `Document`
model carries its own connection globally, initialized once in
`shared/db.py`. This is a real, intentional asymmetry with the SQLAlchemy
repositories (which do take an injected `session_factory`), not an
inconsistency to fix.

## Mandatory Rules

- No code outside an `infrastructure/` module queries Mongo or Postgres
  directly — always through a `Protocol`-typed port.
- Conversion helpers (`_to_entity`, `_to_document`, `_to_row`) stay private to
  their infra module.
- `application/` and `entrypoints/` only ever hold domain entities — never a
  `Document` or an ORM row.
- Repository/port instances are constructed once at the composition root
  (`entrypoints/cli.py`, `entrypoints/api.py`) and passed into the use case —
  not created fresh per call.

## No multi-tenant scoping

There is no `company_id` or tenant filter to enforce — every query is already
scoped to the single portfolio this tool manages. Don't add tenant plumbing
speculatively.

## Anti-Patterns

```python
# BAD — direct SQLAlchemy query inside a use case
async def execute(self, ticker: str) -> TickerAnalysis:
    result = await session.execute(select(TickerAnalysisRow).where(...))
    ...

# BAD — leaking the ORM row as a return type
async def latest(self, ticker: str) -> TickerAnalysisRow: ...  # should be TickerAnalysis | None
```
