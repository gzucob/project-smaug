---
description: Domain entities, persistence models (Mongo + Postgres), and API DTO conventions
applies_to: src/smaug/**/domain/entities.py, src/smaug/**/infrastructure/models.py, src/smaug/**/infrastructure/sqlalchemy_models.py, src/smaug/entrypoints/api.py
---

# Entities, Persistence Models, and DTO Rules

## Domain Entities

All domain entities are **immutable frozen dataclasses**:

```python
@dataclass(frozen=True)
class TickerAnalysis:
    ticker: str
    sector: Sector
    reference_date: date
    ...
```

- Always `@dataclass(frozen=True)`.
- `RawIngestion` (`ingestion/domain/entities.py`) additionally uses
  `slots=True` for its hot append-only path; other entities (`TickerAnalysis`,
  `Indicators`, `StandardizedFinancials`) don't. Add `slots=True` to a new
  entity that's created in bulk or in a loop; it isn't mandatory everywhere.
- Use `dataclasses.replace()` to derive a modified copy instead of adding
  mutation methods.
- `None` means "not applicable to this sector" or "input missing" — never a
  sentinel like `0` or `""` for a numeric/decimal field (see `Indicators`'
  docstring for the reasoning).
- Entities live in `<context>/domain/entities.py`, or in a dedicated file for
  a context-specific value object (`financials.py`, `indicators.py`). They
  never import from `infrastructure/` or `entrypoints/`.

## Persistence Models

Two storage technologies, two model shapes — both live exclusively in
`<context>/infrastructure/`:

- **MongoDB (Beanie `Document`)** — `ingestion/infrastructure/models.py`
  (e.g. `RawIngestionDocument`). Append-only collections; indexes declared in
  a nested `class Settings`.
- **PostgreSQL (SQLAlchemy)** — `analysis/infrastructure/sqlalchemy_models.py`
  (e.g. `TickerAnalysisRow`).

Neither is ever returned from a repository/port implementation — every
implementation converts at its own boundary, using the vocabulary of its own
storage:

- Mongo: `_to_document()` / `_to_entity()` (see `BeanieRawIngestionRepository`)
- Postgres: `_to_row()` / `_to_entity()` (see `SqlAlchemyAnalysisRepository`)

These conversion functions are private to their infra module (module-level
functions or `@staticmethod`) — never imported or called from
`application/` or `entrypoints/`.

## API DTOs (`entrypoints/api.py`)

Response models are plain Pydantic `BaseModel` — not frozen. (Some stricter
DDD templates mandate `model_config = {"frozen": True}` on every DTO; that
isn't what `api.py` does today, so this file describes the actual
convention rather than a rule to retrofit.) Built from a domain entity via an
explicit `_to_response()` function — never return a domain entity or a
persistence model directly from a route:

```python
def _to_response(analysis: TickerAnalysis) -> AnalysisResponse:
    return AnalysisResponse(ticker=analysis.ticker, ...)
```

## Language

All identifiers (field, class, variable, function names) are in English —
matches the project-wide rule in `CLAUDE.md`. PT-BR is reserved for
documentation prose.
