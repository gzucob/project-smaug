# 0027 — The per-share history is split-adjusted onto the current base

- **Status:** Accepted
- **Date:** 2026-07-16

## Context

Share counts come from the FRE **as filed** for each year (ADR 0004/0017).
BBAS3 files 2.87 bn shares through 2022 and 5.73 bn from 2023 — the 2:1 bonus —
so the closed-year LPA/VPA series had a genuine step at the event: each year
faithful to its own filing, the series as a whole discontinuous and
incomparable with any platform chart, which restates history onto the current
base (#76).

The step was not only cosmetic. The closed-year price source back-adjusts
every close for splits (Yahoo does this to its `close` series before we ever
see it), so a pre-bonus year paired an **adjusted price** with an **as-filed
count**: BBAS3's pre-2023 caps — and every multiple built on them — came out
exactly half. The count and the price must sit on the same share base, and the
price's base is, irrevocably, the current one.

Detection has to come from the filings, because the FRE never labels a split.
The signature is arithmetic: a corporate action on the whole base (split,
grupamento, bonificação) multiplies the count by a **clean small rational,
exact to the share** — BBAS3 ×2 to the digit, SANEPAR 2020 ×3, HAPVIDA's 2025
grupamento ÷15 within the fraction the company rounded away, LREN3's 2024
bonificação ×11/10. A real issuance is dirty: HAPVIDA's 2022 merger multiplied
the count by 1.8354. Capital value cannot be the discriminator — a bonificação
capitalizes reserves (BBAS3's moved 90 → 120 bn) while a grupamento leaves it
untouched.

## Decision

Closed-year share counts are **restated onto the current share base** at read
time, in the one chokepoint every consumer already reads through
(`MongoSharesReader`, applied to the per-share denominator and to the class
counts the cap sums — ADR 0014):

- Between consecutive filed FRE years, a count ratio that is a clean rational
  (denominator ≤ 20, exact to a relative 1e-6) is a corporate action; the
  factors compound from each year forward to the latest filed year, which is
  the base and maps to 1.
- A dirty ratio (issuance, buyback cancellation) contributes factor 1: those
  shares changed hands, and restating them would rewrite a dilution as a
  bonus.
- Treasury is netted at the year's own base first; the factor applies to the
  outstanding result (ADR 0017 is unchanged).
- The **as-filed reading stays derivable**: the mirror keeps every filing
  (ADR 0016) and the factor is recomputed on every read, never stored.

## Consequences

- The LPA/VPA history is continuous and comparable with the platforms'; the
  pre-bonus BBAS3 caps (and every multiple on them) stop being 2× understated
  against the split-adjusted price series.
- HAPV3's 2024 per-share figures land on the post-grupamento base the platform
  publishes; what remains of that divergence is the issued-vs-outstanding
  choice (ADR 0017), not the 15× step.
- What this costs: a year's published `shares` is no longer the number in that
  year's FRE — it is that number restated. The mirror keeps the original; the
  UI must label the basis (the front-end follow-up of ADR 0026 covers both).
- Known limitation, accepted: a **composite action in a single FRE year**
  (VIVT3 2024: a 2:1 split and a 43.9 M cancellation, combined ratio 1.9734)
  is dirty and escapes detection — those pre-2024 VIVT3 years keep the old
  base, exactly as they did before this ADR. Event-level detection inside the
  FRE's capital history would catch it and is deliberately not attempted here:
  the member's rows are internally inconsistent (BBAS3's 2022 FRE files the
  120 bn capital against the pre-bonus count). Tracked as an issue.
- A real issuance of an exactly clean proportion (a rights issue of precisely
  1:10, to the share) would be misread as a bonus. Nothing filed
  distinguishes the two; the fidelity gate is the backstop.
