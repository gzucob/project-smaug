# 0004 — The share count comes from CVM's FRE, per fiscal year

- **Status:** Accepted
- **Date:** 2026-07-09
- **Provenance:** recorded retroactively on 2026-07-10 from F12 of
  `docs/FINDINGS_INDICATORS.md` (deleted; recoverable in git history). See `.claude/RULES/RULES_DOCS.md`.

## Context

`eps` (LPA) and `bvps` (VPA) need a share count. brapi's free-plan quote does not
return `sharesOutstanding`, so both were null for every ticker and every view.

The obvious fallback was to derive it from the identity `market cap ≡ price ×
shares`, and issue #22 proposed exactly that, calling it exact. **Probing the
live quote disproved the premise.** brapi returns the same *company-wide*
`marketCap` for every share class of a company: PETR3 and PETR4 both report
R$ 539,240,668,702.99, at prices of R$ 43.53 and R$ 39.21. Dividing that one cap
by each price yields 12.39 bn and 13.75 bn shares — neither is the real 12.89 bn.
The identity only holds for a single-class ticker. Measured against the counts
CVM has on file, `cap / price` was off by +6.7% for PETR4, −8.0% for BBDC4, and
−6.7% for VALE3, in *both* directions.

**The share count is not in the financial statements.** BPA, BPP, DRE, and DFC
carry no share-count account, so no amount of remapping would have found it. CVM
publishes it in the **FRE** (*Formulário de Referência*), a separate yearly ZIP.

The DRE *does* carry a reported earnings-per-share line per class (`3.99.01.01`
ON, `3.99.01.02` PN), which would be more faithful still — it is the company's
own figure. Its coverage is uneven: **BBAS3 files it as zero**, and **VALE3 files
its value in the PN slot** despite having only ON shares.

## Decision

Share counts are read from the FRE's `fre_cia_aberta_capital_social_*.csv`, one
ZIP **per year**, and each view divides by the count filed **for its own fiscal
year**. The `Capital Integralizado` (paid-in) row is the one that reflects shares
in existence; `Capital Emitido` and `Capital Subscrito` differ and are ignored.

The DRE's reported per-class EPS is **rejected as the primary source**, on
coverage.

Per-share indicators are **null for units** (`SAPR11`, `TAEE11`). A unit is a
bundle of share classes (SAPR11 = 1 ON + 2 PN — its filed counts are exactly
1:2), so its quoted price is the bundle's while CVM counts the underlying shares.
Dividing by the share count would produce a figure that does not line up with the
price. `portfolio/domain/share_classes.py`.

`cap / price` survives as a **logged fallback** in `BrapiPriceProvider` for a
ticker with no FRE row at all, with the bias measured above.

## Consequences

- **The multiples were never affected and remain unaffected.** `pe = cap /
  net_income` and `pb = cap / equity` are computed from the market cap directly
  and never touch `shares` (ADR 0001). Only the absolute per-share values were
  ever at risk. Had the multiples been rebuilt from a share count, this bug would
  have silently corrupted the project's headline numbers.

- **A real historical series exists.** One ZIP per year means each closed-year
  view divides by the count filed for that year, retiring the earlier
  approximation (current count applied to a past year's earnings). The series is
  visibly right: PETR4 goes 13.04 bn (2021–23) → 12.89 bn after the
  cancellations; VALE3 5.00 → 4.44 bn.

- **The FRE is keyed by CNPJ, not by the `CD_CVM` the statements use.** This
  forces a second curated map in `portfolio/domain/cvm_codes.py`, cross-checked
  against CVM's `cad_cia_aberta.csv` registry — a second thing to maintain per
  ticker, and a second thing that will not scale to the whole B3 without a
  company registry.

- **pycvm cannot read the modern FRE** (`BadDocument: unknown document type
  'FRE WEB'`), so `cvm_capital.py` reads the CSV member out of the ZIP directly.
  This is the second pycvm quirk in the codebase, after the DMPL crash — the
  dependency is load-bearing for the statements and unusable for everything else.

- **Counts are as filed, never split-adjusted.** BBAS3 shows 2.87 bn through 2022
  and 5.73 bn from 2024 (the 2:1 bonus). A closed-year LPA chart therefore has a
  genuine step at the split: faithful to what was reported that year, and *not*
  what a split-adjusted platform series shows. Any comparison of our per-share
  history against a platform's must account for this, or it will read the step as
  an error.

- **A missing or zero filing falls back to the nearest earlier year.**
  `MongoSharesReader` drops non-positive counts (BBAS3 filed `0` for 2023) and
  fills gaps (VALE3 has no 2023/2024 FRE in the mirror) from an adjacent year.
  The view is then priced on its own year but divided by another year's count —
  correct in magnitude, silently approximate in detail.
