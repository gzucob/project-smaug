# 0018 — A closed year's multiples divide by what the shares traded at, not by the adjusted series

- **Status:** Accepted
- **Date:** 2026-07-14

## Context

A closed year's multiples were priced on that year's **dividend-adjusted** average
(ADR 0001, carried into 0012 and 0014). The adjusted series discounts every past
price by the payouts made since, so the further back you look at a heavy payer, the
more it collapses. Once every closed year was actually priced (#42, via Yahoo), the
consequence became visible and absurd:

| PETR4 | adjusted avg | nominal avg | P/E (adjusted) | DY (adjusted) |
|---|---|---|---|---|
| 2021 | 7.93 | 26.86 | 1.00 | 67.8% |
| 2022 | 13.15 | 30.67 | 0.97 | **105.9%** |

A dividend yield above 100% does not describe how a company was valued: nobody bought
PETR4 in 2022 at R$13.15. The adjusted price answers *"what did I earn holding this
since then"* — a return question, asked from today. A valuation multiple asks *"what
was the market paying for this company that year"* — a question whose answer was
fixed at the time, and cannot change because a dividend was paid afterwards.

The original decision rested on one check: PETR4's 2024 adjusted figure matched AUVP
(F7). Checked across more years against a reference platform (Dados de Mercado), the
picture inverts. That platform's **dividend yield** per year matches the *nominal*
basis almost exactly — 2021: 19.87% against our 20.0% — while its **P/E** chart for
the same year only reconciles against an adjusted price. The platform is internally
inconsistent: its P/E history is the artifact, and its DY, computed the honest way,
agrees with the nominal basis.

## Decision

Every valuation multiple divides by the price the shares **actually traded at**:

- the live TTM view, on the current quote (unchanged);
- a closed year, on that year's **nominal** average — each listed class at its own
  nominal average, times the shares outstanding for that class (ADR 0014/0017).

The dividend-adjusted average stays computed and persisted, in its own column
(`price_adjusted`), as the **total-return** reference. It never reaches the cap. The
live view has no adjusted counterpart at all: nothing has been paid out since a quote
taken now, so there is nothing to adjust it by.

This supersedes the price-basis half of ADR 0001/0012 and refines ADR 0014, whose cap
identity (Σ class price × class shares) is unchanged — only *which* price it sums.

## Consequences

- Historical multiples become interpretable: PETR4 2022 reads P/E 2.25 and DY 45.9%
  rather than 0.97 and 105.9%. No P/E below 1 and no yield above 100% remain.
- Our P/E history will **not** line up with the platforms' P/E charts, which are drawn
  on adjusted prices. That is a deliberate, defended divergence — and #44's fixture
  must pin it as such, per-indicator, rather than treating it as a failure.
- The heavier the payer, the bigger the change: PETR4 and BBAS3 move most, WEGE3
  barely at all.
- A future total-return screen has its input already stored; nothing needs recomputing
  for it.
- Migration 0006 renames `price_nominal` to `price_adjusted` and swaps the two stored
  values, so rows computed before this ADR keep their meaning.
