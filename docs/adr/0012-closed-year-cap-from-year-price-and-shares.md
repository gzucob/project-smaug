# 0012 — Closed-year cap is built from the year's price and filed shares

- **Status:** Accepted (supersedes [0001](0001-closed-year-pricing-basis.md))
- **Date:** 2026-07-10

## Context

ADR 0001 priced a closed year by **repricing the live market cap** onto the
year's dividend-adjusted average:

```
cap_for_year = current_cap × adjusted_avg_price / current_price
```

It chose this to avoid dividing by a historical share count, which at the time
was unreliable — brapi's company-wide market cap was unusable as a share-count
source (ADR 0004). Two properties of that choice have since become liabilities:

- **It anchors on the live quote.** `current_cap` and `current_price` come from
  brapi. So a closed-year row is not reproducible from the database alone (it
  depends on the quote the day `analyze` ran), and — worse — it is lost entirely
  whenever the live quote is unavailable. brapi is chronically unstable, and its
  `_market_for_year` short-circuited to null the moment the quote was missing,
  *before* even consulting the history source. Observed 2026-07-10: with brapi
  timing out, a full `analyze` produced `missing_price` for every closed year,
  overwriting previously-good prices.
- **It silently used the *current* share count.** `current_cap / current_price`
  is the current share count, so the repriced cap already divided the year's
  adjusted price by *today's* shares — the wrong year's count for a company
  whose share base changed.

Since then, the closed-year *price* is sourced from Yahoo (ADR 0011) and the
filed **per-year** share count from CVM (ADR 0004) — both are historical facts
about the year, available without the live quote.

## Decision

A closed year's market cap is built from that year's own facts:

```
cap_for_year = adjusted_avg_price (Yahoo) × shares(year) (CVM)
```

- `_market_for_year` no longer takes the live quote as a pricing input. It
  always consults the history source; the `quote` is used only as the
  share-count fallback when CVM filed none for that year.
- The dividend-adjusted average remains the price basis (ADR 0001's price
  decision stands); only the *cap construction* changes. The DFP earnings/equity
  basis (0001's second decision) is likewise unchanged.
- A null cap is now attributed precisely: `missing_price` when the year price is
  absent, `missing_share_count` when the price is present but no share count was
  filed — keeping the null-reason vocabulary (ADR 0008) honest.

The live TTM view is unchanged: it legitimately prices on the current quote.

## Consequences

- **Closed-year multiples survive a live-quote outage.** With Yahoo history and
  a CVM share count, P/E, P/B, PSR, price-to-assets, EV/EBITDA and dividend
  yield compute even when brapi is down. This is the resilience #66 targeted.
- **A closed-year row is reproducible from the database.** Both inputs are
  historical; re-running `analyze` on another day yields the same historical cap
  (unlike 0001, which drifted with the current quote).
- **Per-year shares improve accuracy**: the cap divides the year's price by that
  year's filed count, not today's.
- **The multiples now depend on the share count** — the exposure ADR 0001
  deliberately avoided. This is acceptable because CVM's filed per-year count is
  the authoritative source (ADR 0004), not brapi's company-wide cap; where it is
  absent the multiple degrades to a *named* `missing_share_count` null rather
  than a wrong value. Absolute per-share figures (LPA/VPA) were already exposed
  to the same count.
- Dividend yield still divides the year's payout by this cap, so ADR 0001's note
  about extraordinary-payout years reading as very high closed-year DY stands.
