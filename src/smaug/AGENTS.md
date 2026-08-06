# Backend Engineering Rules

These rules apply to Python code under `src/smaug/`. The root `AGENTS.md`
remains in force.

## Architecture and Layering

### DDD Lite — isolated contexts, not a monolith of layers

`src/smaug/` is organized as isolated bounded contexts. Each context owns its
own `domain/`, `application/`, and `infrastructure/`; contexts never import
each other's internals directly.

| Context | Responsibility |
|---|---|
| `ingestion` | Faithful, uninterpreted mirror of CVM/B3 raw data into MongoDB |
| `analysis` | Derives financials, computes indicators and persists to PostgreSQL |
| `portfolio` | Static ticker, sector and CVM-code lookup; no persistence |
| `shared` | Configuration, database connections, events, logging and errors |
| `entrypoints` | Typer CLI and read-only FastAPI composition roots |

The layer hierarchy is:

```text
domain/          <- pure logic: frozen entities, Protocol ports, calculators
application/     <- use cases: orchestrate ports, no I/O of their own
infrastructure/  <- Mongo, PostgreSQL, HTTP clients and external parsing
entrypoints/     <- CLI and API composition roots, no business logic
```

- Domain never imports infrastructure libraries such as Beanie, SQLAlchemy or
  httpx.
- Application depends only on domain ports, never on concrete infrastructure.
- Entrypoints construct concrete dependencies and call use cases; they do not
  compute or transform business data.
- Infrastructure models never cross into application or entrypoints.

### Cross-context communication

`src/smaug/shared/events.py` defines an in-process synchronous `EventBus`.
Use publish/subscribe through this bus, or thread data through the entrypoint
composition root, rather than adding direct cross-context imports.

### Ports and repositories naming

`ingestion/domain/repositories.py` and `analysis/domain/ports.py` both define
`Protocol` dependency boundaries. Use `repositories.py` when the only external
dependency is storage, and `ports.py` when the boundary also includes another
service such as a price provider.

### No multi-tenancy

This is a single-portfolio personal tool. Do not add `company_id`, tenant
filters or other multi-tenant plumbing speculatively.

## Domain Entities

All domain entities are immutable frozen dataclasses:

```python
@dataclass(frozen=True)
class TickerAnalysis:
    ticker: str
    sector: Sector
    reference_date: date
    ...
```

- Always use `@dataclass(frozen=True)`.
- `RawIngestion` additionally uses `slots=True` for its hot append-only path.
  Add slots to a new entity created in bulk or in a loop; it is not mandatory
  everywhere.
- Use `dataclasses.replace()` to derive a modified copy instead of mutation
  methods.
- `None` means not applicable or input missing; never use `0` or `""` as a
  sentinel for a numeric or decimal field.
- Entities live in `<context>/domain/entities.py` or a dedicated domain value
  object module. They never import infrastructure or entrypoints.

## Persistence Models

Two storage technologies have two model shapes, both exclusively inside the
owning context's infrastructure:

- MongoDB uses Beanie `Document` models such as `RawIngestionDocument` and
  append-only collections with indexes declared in `class Settings`.
- PostgreSQL uses SQLAlchemy rows such as `TickerAnalysisRow`.

Persistence models are never returned from a repository or port. Each
implementation converts at its boundary:

- Mongo: `_to_document()` and `_to_entity()`.
- PostgreSQL: `_to_row()` and `_to_entity()`.

These conversion helpers remain private to their infrastructure module.

## API DTOs

Response models in `entrypoints/api.py` are plain Pydantic `BaseModel`
instances, not frozen. Build them from domain entities through an explicit
private conversion function:

```python
def _to_response(analysis: TickerAnalysis) -> AnalysisResponse:
    return AnalysisResponse(ticker=analysis.ticker, ...)
```

Never return a domain entity or persistence model directly from a route.

## Repository and Port Pattern

### Interfaces are Protocols, not ABCs

Every dependency boundary in `domain/` is a `typing.Protocol`, enabling
structural typing without forcing implementations or test doubles to inherit:

```python
class FundamentalsReader(Protocol):
    async def history(self, ticker: str) -> list[StandardizedFinancials]: ...
    async def annuals(self, ticker: str) -> list[StandardizedFinancials]: ...
```

Do not replace these with abstract base classes.

### Implementations

Implementations live in `<context>/infrastructure/`, receive storage handles
through constructor injection and are never instantiated inside a use case:

```python
class SqlAlchemyAnalysisRepository:
    def __init__(
        self, session_factory: async_sessionmaker[AsyncSession]
    ) -> None:
        self._session_factory = session_factory
```

Beanie repositories do not take a session parameter because the document model
carries the connection initialized in `shared/db.py`. This asymmetry with
SQLAlchemy's injected session factory is intentional.

Mandatory boundaries:

- Only infrastructure modules query MongoDB or PostgreSQL directly.
- Conversion helpers remain private to their infrastructure module.
- Application and entrypoints hold domain entities, never documents or ORM
  rows.
- Repository and port instances are constructed once at the composition root
  and passed into use cases.

Anti-patterns:

```python
# BAD: direct SQLAlchemy query inside a use case
async def execute(self, ticker: str) -> TickerAnalysis:
    result = await session.execute(select(TickerAnalysisRow).where(...))
    ...

# BAD: leaking an ORM row
async def latest(self, ticker: str) -> TickerAnalysisRow: ...
```

## Typing and Code Style

### mypy strict

`mypy --strict` must pass with zero errors. There are no blanket
`[[tool.mypy.overrides]]` ignores. A new override requires a concrete
third-party typing limitation and an adjacent justification.

### Type annotations

```python
# CORRECT
def find_latest(self, ticker: str, module: str) -> RawIngestion | None: ...

# WRONG
from typing import Optional, Union
def find_latest(self, ticker: str) -> Optional[RawIngestion]: ...
```

- Use `X | None`, never `Optional[X]`.
- Use `X | Y`, never `Union[X, Y]`.
- Use `Any` only with an inline justification for genuinely untyped input.

### Docstrings

The codebase does not use full Google-style `Args:`, `Returns:` or `Raises:`
blocks.

- Module docstrings are one line, with a short rationale paragraph only when a
  design choice is not obvious.
- Public class and function docstrings are one line, with rationale only when
  the signature and name do not explain it.
- Private helpers usually need no docstring when their names are clear.

### Code style

- Maximum line length is 88.
- Ruff selects `E, F, I, N, W, UP, B, C4, PT`.
- Imports are ordered standard library, third party, then local.
- Prefer pure deterministic functions in `domain/`.
- Prefer composition and Protocol structural typing over inheritance.
- All identifiers, code comments and docstrings are in English.

Before committing backend changes, run the quality gate in
`.github/AGENTS.md`.
