# 0011 — Yahoo Finance sources the closed-year price history

- **Status:** Accepted (supersedes [0007](0007-closed-year-price-coverage-is-plan-gated.md))
- **Date:** 2026-07-10

## Context

ADR 0007 established that closed-year price coverage was bounded by the brapi
**free plan**: `GET /quote/{ticker}?range=5y&interval=1d` returns the full daily
series only for the plan's demo tickers (PETR4, VALE3), answering HTTP 400
`INVALID_RANGE` for every other ticker. The permitted ranges top out at three
months, which cannot cover a fiscal year, so seven of nine portfolio tickers
carried null closed-year prices — and therefore null historical P/E, P/B, PSR,
DY and EV/EBITDA. 0007 deferred the fix to #50: upgrade the brapi plan, or add
an alternative history source behind `PriceProvider.year_prices()`.

Two forces settled the choice. The **live quote** side is not affected — brapi's
basic `GET /quote/{ticker}` serves a current price and market cap for every
ticker on the free plan — so only the multi-year *history* needs another source.
And the history requirement is about to grow: coverage is planned to extend from
five to ten closed years, and the portfolio will eventually include tickers that
have left the exchange (delisted, merged, renamed), whose history a single
paid range would not necessarily restore.

## Decision

The closed-year daily history is sourced from **Yahoo Finance's public chart
endpoint** (`/v8/finance/chart/{symbol}`), while brapi remains the sole source
of the live quote. Concretely:

- A new `PriceHistoryProvider` port owns `year_prices()`; `YahooPriceHistory`
  implements it. brapi's `BrapiPriceProvider` keeps `PriceProvider` (quote +
  its own `year_prices`, still valid for a future paid plan), and a
  `CompositePriceProvider` routes `get()` to brapi and `year_prices()` to Yahoo
  so the analysis use case still depends on one `PriceProvider`.
- The year is requested by **exact window** (`period1`/`period2` timestamps),
  never a fixed `range=Ny`. Extending coverage to more closed years is a wider
  window, not a new range ceiling.
- Yahoo is the history source for **all** tickers, including PETR4 and VALE3, so
  the closed-year basis is uniform rather than mixed across two vendors.
- A symbol Yahoo does not resolve (a delisted ticker) or a year with no trading
  yields `YearPrices()` (null) — an expected, non-fatal absence. Only a
  transport failure raises (as `BrapiTimeoutError`), keeping a mid-run timeout
  distinguishable from a genuine absence.
- The B3 → Yahoo symbol map is the `.SA` suffix (`PETR4` → `PETR4.SA`).

## Consequences

- Closed-year price coverage is no longer capped at the two demo tickers: all
  nine portfolio tickers can carry historical multiples, lifting the 10-of-45
  ceiling 0007 recorded (subject to Yahoo actually serving each symbol/year).
- A second external price dependency is introduced. It is unofficial and
  unauthenticated: it needs a browser-like `User-Agent`, can rate-limit, and
  its response shape is not contractual. The blast radius is contained to
  `YahooPriceHistory` and it degrades to null rather than failing the run.
- brapi's `year_prices` is retained but no longer wired in production; it stays
  a complete `PriceProvider` for the paid-plan path config already anticipates.
- The `smaug doctor` coverage report will reclassify the seven gated tickers'
  closed-year cells from plan-withheld nulls to values (or to a Yahoo-absence
  null), which is the intended M0 progression.
- Open follow-ups this raises: extending ingestion + pricing to ten closed
  years, and handling tickers no longer listed (symbol drift / no Yahoo data).
