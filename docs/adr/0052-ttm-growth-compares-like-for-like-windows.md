# 0052 — TTM growth compares like-for-like trailing windows

- **Status:** Accepted
- **Date:** 2026-08-10

## Context

ADR 0005 made the latest closed DFP the comparison base for revenue and net
income growth in the live TTM view. That is comparable only when the TTM ends in
December. At an interim close, it compares a moving twelve-month window with a
different calendar-year window and folds seasonality into the reported growth.

The CVM filings already contain enough history to reconstruct the correct base:
the four quarters ending on the same fiscal date one year earlier. As with the
current TTM, the missing fourth quarter is derived from the annual DFP less the
first three isolated quarters.

## Decision

- Live TTM revenue and net-income growth compare the current trailing twelve
  months with the trailing twelve months ending on the same fiscal date in the
  prior year.
- The prior window follows the existing flow-isolation rules, including separate
  DRE, DFC and DMPL spans and the annual-derived fourth quarter.
- If the exact prior four-quarter window cannot be assembled, growth is null with
  `MISSING_PRIOR_PERIOD`. A closed DFP is not a fallback for an interim TTM.
- Closed-year views continue to compare one annual DFP with the immediately prior
  annual DFP. Their basis was already like-for-like.
- CAGR continues to use only closed exercises; a TTM window is not inserted into
  the annual series.

This supersedes only ADR 0005's TTM growth-comparison decision. Its account
mapping, controller-share, cash, dividend and period-isolation decisions remain
accepted.

## Consequences

- Q1, Q2 and Q3 live growth no longer includes a calendar-window mismatch.
- Computing TTM growth requires enough older ITR and DFP history to reconstruct
  the prior window; limited history therefore produces an explicit null instead
  of a plausible but incompatible percentage.
- December TTM growth and closed-year growth use equivalent twelve-month endpoints
  and therefore agree when they read the same standardized flows.
