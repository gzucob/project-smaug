# 0066 — Analysis outcomes live outside `ticker_analysis`

- **Status:** Accepted
- **Date:** 2026-08-28

## Context

An analysis run can have a meaningful result for a ticker without producing an
indicator row. For example, the mirrored CVM fundamentals may not contain a
complete TTM window, or the B3 evidence may prove that the available exercises
cannot be named or priced. The result still needs to be available to a later
`smaug doctor` invocation.

Adding a synthetic null row to `ticker_analysis` would confuse an execution
outcome with a financial exercise and would change the cell population used by
coverage metrics. Keeping the outcome only in the command's stdout loses the
reason as soon as the command exits. A later run can also change a ticker from
skipped to analyzed or error, so a diagnostic reader must not retain an older
skip reason as if it were current.

## Decision

The analysis context persists one immutable outcome per ticker and run in a
dedicated PostgreSQL `analysis_outcomes` table. Each row carries the run ID,
ticker, status (`analyzed`, `skipped`, or `error`), an optional named
no-analysis reason, detail text, and the stable timestamp assigned to the run.
The table is separate from `ticker_analysis`; it never represents an indicator
cell and never creates an artificial analysis view.

`AnalyzePortfolioUseCase` is the only writer. It assigns one run ID and one
timestamp to the execution, records an outcome for every requested ticker, and
records an analyzed outcome even when views were produced. `doctor` may read
the latest outcome through an optional port, but it never recomputes or writes
one. Repositories and older fakes that do not expose the optional outcome
surface remain usable.

The latest outcome for a ticker is selected by timestamp, with the database row
ID as a deterministic tie-breaker. Therefore every later analyzed or error
outcome replaces an earlier skipped reason in diagnostics. Only the latest
named skipped outcome is rendered as a ticker-level diagnostic; outcome data is
not added to indicator-cell counts or percentages.

## Consequences

The doctor report can explain why a ticker has no persisted views after the
analysis process has finished, while its existing cell denominators remain
unchanged. Re-running analysis also clears stale skip explanations by writing a
new analyzed or error outcome.

The outcome table is append-only and requires a migration and an additional
read path. It records execution status, not a durable calculation snapshot, so
indicator values and their provenance continue to belong exclusively to
`ticker_analysis`.
