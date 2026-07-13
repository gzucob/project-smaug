# 0014 — The market cap is summed over the company's listed share classes

- **Status:** Accepted (supersedes [0012](0012-closed-year-cap-from-year-price-and-shares.md))
- **Date:** 2026-07-13

## Context

ADR 0012 built the cap as `price × shares(year)` — one quote times every share the
company filed. That identity holds only for a company with a single listed class.
Five of the nine portfolio tickers are fine; the other four are not:

- **A dual-class company gets the wrong price on half its shares.** Petrobras
  lists PETR3 (ON) and PETR4 (PN), which trade at different prices. Capitalizing
  it at PETR4's quote pays the PN price for 7.4 bn ordinary shares. Measured
  2026-07-13 against the summed classes: PETR4's cap ran **7.2% low**, BBDC4's
  **6.5% high**. brapi's own `marketCap`, used as a fallback, has the same defect
  from the other end — it is one company-wide number served for every class, so
  the share count derived from it (`cap / price`) is equally off.
- **A unit has no share count to multiply at all.** SAPR11 and TAEE11 quote a
  *bundle* of shares, so `MongoSharesReader.outstanding` correctly returns `None`
  for them — and under 0012 that nulled the cap, and with it P/L, P/VP, PSR and
  EV/EBITDA, for every unit exercise. The bundle's composition is unknown (#38),
  and 0012 made the multiples hostage to it.

Both are the same mistake — treating a quote as if it priced the company rather
than one class of its shares — and both dissolve under one identity.

## Decision

The market cap is the sum of the company's listed share classes, each at its own
price:

```
cap = Σ over listed classes (class price × shares filed for that class)
```

- `portfolio/domain/share_classes.py` maps each ticker to the classes its company
  lists (`PETR4 → PETR3 ON + PETR4 PN`; `SAPR11 → SAPR3 ON + SAPR4 PN`;
  `WEGE3 → WEGE3 ON`). A single-class company sums one term and is unchanged.
- The counts come from CVM's FRE, which already files them per class (ADR 0004);
  `SharesReader.counts` serves that split, for units too.
- The prices come from wherever that view prices: the **current quote** per class
  for the live TTM, the **year's dividend-adjusted average** per class for a
  closed year. So 0012's price basis and its independence from the live quote both
  stand — only the cap's construction changes.
- **A unit is capitalized without modelling its bundle.** Summing the underlying
  classes never mentions the bundle, so the multiples no longer wait on #38. The
  per-share indicators (LPA/VPA) still do, and stay a named null.
- **The vendor's cap and share count are refused.** brapi's `marketCap` and its
  `cap / price` share count are no longer read. A missing CVM filing yields a
  named null, not a number that is knowably a few percent wrong.
- A missing class price or class count nulls the **whole** cap, never a partial
  company; the use case names which of the two it was, so the null keeps a cause
  (ADR 0008).

## Consequences

- **The dual-class multiples move, and that is the point.** P/L, P/VP, PSR,
  price-to-assets, EV/EBITDA and dividend yield shift for PETR4 (+7.2% cap) and
  BBDC4 (−6.5%). The single-class tickers are bit-for-bit unchanged, which is what
  makes the shift attributable.
- **Units get their multiples back.** SAPR11 → R$ 11.4 bn, TAEE11 → R$ 14.3 bn,
  both matching the market. `missing_share_count` nulls fall from 138 to 24 — the
  24 being exactly LPA/VPA for the two units, i.e. #38 and nothing else.
- **The cap now costs one HTTP call per extra class.** A dual-class ticker fetches
  its sibling's quote (and, per closed year, its sibling's year history). Only the
  four multi-class tickers pay it.
- **A new ticker needs its classes registered**, or its cap is a named null rather
  than a wrong number. `test_every_portfolio_ticker_has_its_listed_classes` fails
  loudly if the map falls behind the portfolio.
- **The counts remain as filed, not split-adjusted.** BBAS3's 2:1 bonus is a real
  step in its per-share history. Whether the closed-year charts should be
  split-adjusted is a separate, undecided question (#76).
- A company that lists a class we do not register (a PNA/PNB, #72) would be
  capitalized short. None exists in this portfolio.
