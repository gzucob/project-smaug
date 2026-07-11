# 0013 — Yahoo is the primary price source; brapi is the fallback

- **Status:** Accepted (supersedes [0011](0011-yahoo-finance-for-closed-year-price-history.md))
- **Date:** 2026-07-10

## Context

ADR 0011 moved the closed-year *history* to Yahoo but left brapi as the **sole
source of the live quote**, and left brapi's own `year_prices` unwired. That kept
the whole live TTM view — and, before ADR 0012 decoupled it, the closed-year
prices too — dependent on brapi. brapi is chronically unstable (its dashboard is
slow and frequently incomplete; from some networks its API does not respond at
all). A run on 2026-07-10 with brapi timing out produced `missing_price` for
every ticker.

Meanwhile Yahoo's chart `meta` exposes `regularMarketPrice` for any symbol
without auth — the same unofficial endpoint already used for history. It does
**not** expose market cap or share count (the richer `v7/finance/quote` now
requires a crumb, HTTP 401), but the cap is no longer needed from the quote:
ADR 0012 already builds the cap from `price × filed shares`, and CVM supplies the
per-year share count (ADR 0004).

## Decision

Yahoo is the **primary** source for both price capabilities; brapi is the
**fallback** for each:

- **Live quote:** `YahooQuoteProvider.get` reads `regularMarketPrice` from the
  chart meta (price only). `FallbackQuoteProvider` tries it first and consults
  brapi only when Yahoo yields no price or fails at the transport/HTTP layer.
- **Year history:** `FallbackPriceHistory` tries `YahooPriceHistory` first and
  falls back to brapi's `year_prices` (which the free plan still serves for its
  demo tickers) — so brapi's history is wired again, but only as reserve.
- **Market cap** is derived, not fetched: `cap = price × shares(CVM)` for the TTM
  view (as ADR 0012 already does for the closed year). When brapi is the quote
  fallback, its own cap is kept.
- The two capabilities stay behind their existing ports
  (`CurrentQuoteProvider`, `PriceHistoryProvider`); the chains are wired at the
  composition root, and `AnalyzePortfolioUseCase` still depends on one
  `PriceProvider`.

This updates `CLAUDE.md`'s "brapi is the price source" framing: brapi is now the
price *fallback*. It remains a configured ingestion source unchanged.

## Consequences

- **The analysis no longer hinges on brapi.** Verified end to end on 2026-07-10:
  a full `analyze` with brapi unreachable priced all nine tickers; `smaug doctor`
  went from `missing_price = 408` to `0`, with value cells 880 → 1160. The
  remaining nulls are honestly attributed — `missing_share_count` where CVM lacks
  a filed count (24 → 138, now distinguishable thanks to ADR 0012's refinement),
  or unclassified zero-denominators on the holdings.
- brapi keeps a real, useful role (plan B), so the free-plan token and its demo
  history still matter — the escape valve the user asked to preserve.
- A second dependence on the same unofficial Yahoo endpoint (now the quote too)
  concentrates risk on it. If Yahoo gates the chart endpoint as it did `v7`, both
  capabilities fall through to brapi; a *contracted* history fallback is tracked
  in #67. The failure mode stays a null, never a wrong value.
- The live quote is price-only, so a ticker with no CVM share count now yields a
  TTM `missing_share_count` (no cap) rather than borrowing brapi's company-wide
  cap — more honest, and consistent with the closed-year basis.
