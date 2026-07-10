# 0003 — Return ratios divide a period flow by the period's closing balance

- **Status:** Accepted
- **Date:** 2026-07-09
- **Provenance:** recorded retroactively on 2026-07-10 from F10 of
  `docs/FINDINGS_INDICATORS.md` (deleted; recoverable in git history). See `.claude/RULES/RULES_DOCS.md`.

## Context

Surfaced while writing the per-indicator reference docs for the front-end
(`frontend/src/lib/indicator-docs.ts`), which forced every formula to be stated
exactly as `calculator.py` computes it.

Three indicators divide a **flow**, earned across the whole period, by a
**stock**, measured at a single instant:

```
roe            = annualized net income / equity        (closing balance)
roa            = annualized net income / total assets  (closing balance)
asset_turnover = annualized revenue    / total assets  (closing balance)
```

The mismatch is real. Where the denominator moved a lot during the period — a
large share issuance, a buyback, or the dividend payment itself — the closing
balance is not what generated the flow. Some references compute these over the
**average** balance (opening + closing) / 2 for exactly this reason.

Choosing the average is not free. It requires the opening balance, which is the
prior year's DFP: an extra filing that must be present in the mirror. Where it is
absent the indicator would degrade to null — trading a *slightly imprecise*
number for *no number at all*, and doing so unevenly across tickers and years.

No divergence against AUVP Analítica or Investidor10 has been measured for our
tickers. The choice is being recorded as what it is: a choice, not a verified
match.

## Decision

`roe`, `roa`, and `asset_turnover` divide by the **closing balance** of the
period. `StandardizedFinancials` already reaches for the prior year's annual
figure through `_prior_year_annual` (it backs the growth indicators), so the
opening balance is reachable if the decision is ever revisited — but the current
formulas do not use it.

## Consequences

- The three indicators are computable from a **single filing**. A ticker with one
  ingested DFP still gets a ROE. Under an average-balance formula it would get
  `null`, and the first year of every ticker's history would be permanently blank.

- Where equity or assets moved sharply within the period, our figure is **biased
  against the closing balance**. A company that issued shares late in the year
  shows a *lower* ROE than an average-balance formula would give it; a company
  that bought back stock or paid a large dividend shows a *higher* one. The bias
  has a sign and a cause, so a divergence from a platform is diagnosable rather
  than mysterious.

- This bias is **largest exactly where it is most visible**: the same
  extraordinary payouts that make the closed-year DY read near 25% (ADR 0001)
  shrink the closing equity that ROE divides by.

- The decision is recorded **without a measurement behind it**. If a cross-check
  against the reference platforms later shows they use the average balance, this
  ADR is superseded by a new one — not edited.
