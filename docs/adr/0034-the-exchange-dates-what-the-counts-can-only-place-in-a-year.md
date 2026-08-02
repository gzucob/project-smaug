# 0034 — The exchange dates what the counts can only place in a year

- **Status:** Accepted
- **Date:** 2026-08-02

## Context

ADR 0033 cut the price series on the day a corporate action took effect, and got
its dates from two places: a **declared** action (ADR 0027 / #174) carries CVM's
approval date, and an **inferred** one — a move read off two consecutive filed
counts — carries no date at all, so it takes the first day of the filing year
that reported it.

That fallback is wrong far more often than it looks, because **the FRE reports an
event a year late**. BBAS3's 2:1 split traded on 2024-04-16; the FRE that first
carries the doubled count is the **2023** one, and CVM stopped filing the
declaration member after that same 2023 form. So the chain placed a real April-2024
event on 2023-01-01 — which left every 2023 session unrestated and every 2024
session restated whole. Measured against the vendor's back-adjusted series:

```
BBAS3 2023   100.0% off      VIVT3 2024   100.0% off
BBAS3 2024    29.9% off      MGLU3 2024    47.5% off
```

Every one of those is a **date** error. The ratios were already right.

B3 publishes the missing half. `GetListedSupplementCompany.stockDividends` lists,
per company, each action's `factor`, its `approvedOn`, and `lastDatePrior` — **the
last session quoted on the old base**, which is the cut a price series actually
needs and which no CVM filing contains. What it does not carry is any share
count, and its history is incomplete in the other direction: it lists **one**
Bradesco bonus (2022) where CVM's file lists nine.

## Decision

Mirror B3's stock-dividend rows (`CAPITAL_EVENT_B3`, as filed, in B3's own
vocabulary and pt-BR number format) and let them **date** the restatement chain:

- An exchange action **never contributes a ratio**. It has no count to anchor
  one on, and a factor applied where the counts saw nothing move is exactly how a
  single split compounded into nine (#174). It moves a date the chain already has.
- It is matched on the **ratio** — the fact both sources state — and adopted only
  where the pairing is unambiguous **in both directions**: one action for that
  step, one step for that action. A company paying a 10% bonus every year offers
  several candidates for any step of 1.1, and an action falling between two
  filing years could belong to either.
- `lastDatePrior + 1` is the effective date: the last old-base session is still
  restated, the first new-base one is not.
- `factor` means two different things and is read accordingly:
  **DESDOBRAMENTO** and **BONIFICACAO** file the *percentage* of new shares (100
  is a 2:1 split, 10 is a 10% bonus), **GRUPAMENTO** files the multiplier itself
  (0.10 is 1:10). Reading one as the other turns a 10% bonus into a tenfold one.
- `CIS RED CAP` and any other label is ignored: a spin-off hands shareholders
  stock in a *different* company, and the base it names is not the one being
  restated.
- A composite action is one row per leg sharing an approval date (VIVT3 2025:
  a ×80 split and a ×0.025 grupamento), so legs are compounded per approval date
  before matching — leg by leg neither is the ×2 both the market and the counts saw.

The counts are untouched: `restatement_factors` does not take this input, and no
share count moves. Only the price's timeline does.

## Consequences

Measured over 8 tickers × 10 years against the vendor series: **20 cells closer,
0 further**, up from 16 under ADR 0033 alone. The four large ones go to zero:

```
BBAS3 2023  100.00% -> 0.00%      VIVT3 2024  100.00% -> 0.00%
BBAS3 2024   29.91% -> 0.00%      MGLU3 2024   47.45% -> 0.85%
```

The ex date also refines every action CVM had already dated by its approval —
WEGE3 2021 0.40% → 0.01%, SAPR4 2020 0.63% → 0.05%, MGLU3 2020 13.9% → 7.1% —
which is the residual ADR 0033 named and could not close.

**A step whose ratio the counts got only approximately is not dated.** Bradesco's
bonuses read 1.0966 off the filed counts (the year's issuance is in there too)
where B3 states a clean 1.1, and the two do not match to 1e-9, so its steps keep
their CVM approval dates. Loosening the tolerance would trade a wrong date for a
wrong event, which is the worse error: this rule buys accuracy only where both
sources agree exactly on what happened.

**What is still missing is a ratio, not a date.** TOTS3's 3:1 split is listed by
both sources and is in neither chain: CVM's declaration says the base went from
192,637,727 to 577,913,181, and no FRE year filed 192,637,727 — so it anchors on
nothing, and the filed step (165,637,727 → 577,913,181) is dirty because an
issuance rode along with it. TOTS3 stays 200% out. Fixing it means letting a
declared action anchor on *either* side of the move, which changes share counts
and therefore needs the exchange-wide diff — a separate change, filed as an issue.
