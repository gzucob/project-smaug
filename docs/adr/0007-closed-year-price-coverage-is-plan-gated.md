# 0007 — Closed-year price coverage is bounded by the brapi plan

- **Status:** Accepted
- **Date:** 2026-07-10

## Context

The closed-year view prices each fiscal year on its dividend-adjusted average
(ADR 0001), built from brapi's daily history:
`GET /quote/{ticker}?range=5y&interval=1d`. Against the live Postgres, only
PETR4 and VALE3 ever carried closed-year prices; the other seven portfolio
tickers had none, in every run, regardless of processing order (#42).

Probing the endpoint directly settled the cause. On the **free plan**, the
`5y` range is only served for the plan's demo tickers — in this portfolio,
PETR4 and VALE3, which return the full five-year daily series with
`adjustedClose` on every point. Every other ticker gets **HTTP 400
`INVALID_RANGE`** ("Ranges permitidos: 1d, 5d, 1mo, 3mo") before any data is
considered. The prices for those tickers were never written; nothing was lost
in a migration and no rate limit was exhausted mid-run. A three-month ceiling
cannot cover a closed fiscal year, so no permitted range is a workaround.

The 400 surfaces as a `BrapiUnexpectedStatusError`, which the use case
catches per year and degrades to null market multiples — by design, so a
price failure never destroys the accounting indicators. That resilience is
what kept the plan boundary invisible: a plan-gated absence persisted the
same nulls as a genuine one. (A separate, transient failure also hides in the
same null: PETR4 lost three years in the run of 2026-07-09 21:02 to a passing
HTTP failure that a later re-run recovered. Making the two absences loud and
distinguishable is #42's remit.)

## Decision

brapi remains the sole price source, on the free plan. Consequently:

- A closed-year row for a ticker whose history request is plan-rejected
  persists **null** price and null market multiples. This is the correct
  representation of an input the plan withholds — never a substituted or
  approximated price.
- The live TTM view is unaffected: the current quote is free-plan-available
  for every ticker, so live multiples keep full coverage.
- Restoring coverage for the gated tickers requires a plan upgrade or an
  additional history source behind `PriceProvider.year_prices()` — a decision
  deferred to #50, and one that would supersede this ADR.

## Consequences

- Closed-year price coverage is capped at the demo tickers — 10 of 45 rows
  (PETR4 and VALE3, five years each) — until #50 resolves. Historical P/E,
  P/B, PSR, DY and EV/EBITDA are null for the other seven tickers, so the
  reference-platform fidelity work (#44) can only pin historical multiples
  for those two.
- The boundary is the *plan's*, not the data's: the fix is commercial or
  architectural, never a parsing change. Re-running `analyze` cannot improve
  on 10 of 45, and a coverage report should treat those nulls as expected
  under the current plan rather than as regressions.
- Re-running `analyze` **does** recover transient losses on the non-gated
  tickers (it restored PETR4 to 5 of 5 on 2026-07-10), which is why absence
  caused by a passing failure must be distinguishable from this plan gate
  (#42).
