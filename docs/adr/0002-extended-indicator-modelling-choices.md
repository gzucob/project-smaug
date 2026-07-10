# 0002 — NOPAT uses a flat statutory tax rate; capex is gross; payout shares its period basis

- **Status:** Accepted
- **Date:** 2026-07-08
- **Provenance:** recorded retroactively on 2026-07-10 from F8 of
  `docs/FINDINGS_INDICATORS.md` (deleted; recoverable in git history). See `.claude/RULES/RULES_DOCS.md`.

## Context

The indicator set grew from 14 to 29 to approach the coverage of AUVP Analítica
and Investidor10. Every new indicator is derived from accounts already mirrored
from CVM — **no new ingestion was required**, including the whole free-cash-flow
family, because the DFC (cash-flow statement) is already stored in full: it is
where `dep_amort` and `dividends_paid` come from.

Three of the new indicators could not be computed without first making a
modelling choice that the source data does not settle.

**ROIC needs an after-tax operating profit.** The faithful figure would be each
company's *effective* tax rate for the period, derived from its own tax line.
That rate swings with deferred taxes, tax incentives, and one-off items, so a
year of an unusual tax event moves ROIC for reasons that have nothing to do with
operating efficiency.

**Free cash flow needs a capex figure.** The DFC's investing section carries both
outflows (buying PP&E and intangibles) and inflows (disposals). Netting them
gives *net* capex; taking only the outflows gives *gross* capex. The two diverge
whenever a company sells assets.

**Payout and dividend yield mix a flow with a flow and a flow with a stock.**
Every other flow-based indicator in `calculator.py` is annualized so that a
year-to-date period reads as comparable to a full year. Applying that same
annualization to a payout ratio would annualize the numerator *and* the
denominator, which is a no-op at best and a distortion when the two spans differ.

## Decision

- **NOPAT = annualized EBIT × (1 − 0.34).** A flat statutory rate (IRPJ 25% +
  CSLL 9%), not the company's effective rate. `_TAX_RATE` in `calculator.py`.
  Invested capital = controllers' equity + net financial debt. Financial-regime
  companies return `null` for ROIC, since net debt is not defined for them.

- **Capex is gross.** `_capex` in `mongo_fundamentals.py` sums the *outflows*
  under DFC code `6.02.*` whose label mentions `imobilizado` or `intangível`.
  Disposals (positive inflows) are ignored. `fcf = CFO − capex`, annualized like
  the other flows and isolated on the DFC's own period span. `null` when either
  leg is missing, so FCF degrades rather than misleads.

- **Payout is not annualized.** `payout = dividends_paid / net_income` with
  numerator and denominator on the *same* period basis — both trailing-twelve-
  month in the live view, both annual in a closed year. `dividend_yield` divides
  the same dividends figure by the market cap.

Also decided at the same time, for the same reason (the source does not settle
it):

- **`price_to_working_capital` is left signed.** It goes negative when current
  liabilities exceed current assets. That is meaningful — it is Graham's basis —
  and clamping it to null would hide a real fact about the balance sheet.

- **Headline financials (`revenue`, `net_income`, `dividends`) are persisted**
  alongside the ratios, as the period's own absolute figure in reais, *not*
  annualized. They are not indicators, but the ratios alone cannot reconstruct
  them, and the front-end charts their per-year evolution.

## Consequences

- ROIC is comparable **across companies and across years** by construction: the
  only thing moving it is operating profit against invested capital. This is also
  how the reference platforms present it, so a cross-check against them is
  measuring the same quantity.

- ROIC is therefore **not** each company's true after-tax return. For a company
  whose effective rate is far from 34% — a large deferred-tax position, a tax
  incentive regime — our ROIC is biased in a known direction and by a knowable
  amount. It answers "how efficiently is capital deployed", not "what did
  shareholders keep".

- Gross capex makes FCF **conservative**: a company that funds part of its
  investment by selling assets shows a lower free cash flow than its net cash
  movement implies. Preferred over net capex because a disposal is a one-off
  financing event, not a reduction in the cost of maintaining the asset base.

- Capex depends on **label matching** (`imobilizado` / `intangível`) rather than
  on a code alone, because CVM's `6.02.*` sub-codes are not stable across
  filers. A filing that words the line differently yields `null` capex and
  therefore `null` FCF for that period — a silent gap rather than a wrong number,
  which is the intended failure mode.

- Because payout is not annualized, it is **only meaningful when numerator and
  denominator span the same period**. Both views satisfy this. Any future view
  that mixes spans (a partial year against an annual dividend) would silently
  produce a wrong payout; the invariant lives in `calculator.py`, not in a type.

- A negative `price_to_working_capital` is a valid value, so **null and negative
  mean different things** for this indicator, and any consumer that treats
  "falsy" as "missing" will be wrong.
