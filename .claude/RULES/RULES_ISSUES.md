# Issue Rules

An issue without a prefix or without the required labels is considered
incomplete.

## Title
`[NAMESPACE-NN] short imperative title (up to 72 chars)`

- `NAMESPACE`: code from the table below. `NN`: two-digit sequence.
- Title in English, imperative mood.

### Namespaces
| Namespace | Area |
|---|---|
| `ING` | Ingestion — brapi client, collection, mirror persistence |
| `ANL` | Analysis — indicator calculation, PostgreSQL persistence, read API |
| `PORT` | Portfolio — ticker → sector map |
| `CORE` | Shared — config, Mongo connection, EventBus, errors |
| `INFRA` | Docker, dependencies, repository configuration |
| `DX` | Tooling, local dev experience |
| `TEST` | Tests, coverage, CI |
| `DOCS` | Documentation |
| `SEC` | Security — secrets, token, exposure |

## Required labels (all three)
- area: `area: ingestion`, `area: analysis`, `area: portfolio`, `area: core`,
  `area: infra`, `area: docs`, `area: testing`
- priority: `priority: high`, `priority: medium`, `priority: low`
- type: `type: feature`, `type: bug`, `type: tech-debt`, `type: security`,
  `type: docs`, `type: chore`

An issue may have more than one `area:`; `priority` and `type` are
single-value.

## Body
```
## Context
## Improvement / Fix
## Implementation Notes (optional)
```

## Closing
In the PR body: `Closes #NN` (GitHub closes the issue on merge).
