# 0026 — Both statement slices are published, paired by slice

- **Status:** Accepted
- **Date:** 2026-07-16

## Context

A consolidated filing carries two versions of its bottom line and its equity:
the **total** (the whole group, minority interest included — DRE 3.11, BPP
2.03 as filed) and the **controllers' slice** (what accrues to the listed
shares — 3.11.01, 2.03 minus 2.03.09). Both are filed numbers; they answer
different questions.

Until now every indicator used the controllers' slice (#78 established how it
is read). The sector-representatives comparison (#116) showed the reference
platform disagreeing on exactly the companies with material minorities
(ABEV3, TOTS3, VALE3, WEGE3) — on margins and ROE, while eps and bvps agreed.

Decoding the platform's published values against the filed statements closed
the arithmetic exactly:

- ABEV3 2024 net_margin: 14,846,952 / 89,452,669 = 0.16598 — its published
  0.1660, to four decimals. The numerator is the **total** (3.11), not the
  controllers' 14,437,238.
- TOTS3 2024 roe: 735,443 / 4,987,121 = 0.14747 — its published 0.1475,
  exactly. **Total over total**, closing balance. (2025 closes the same way:
  0.16915 vs its 0.1691.)
- TOTS3 bvps: 7.81 × its implied count ≈ the filed **controllers'** equity
  4,681,352 — per-share figures stay on the controllers' slice.

So the platform is not inconsistent: per-share figures use the controllers'
slice, whole-firm ratios use the consolidated total. There is a structural
reason to do the same: **revenue and total assets only exist as consolidated
totals** — no controllers' revenue is filed — so a margin with a controllers'
numerator mixes slices by construction. And symmetrically, the share count
and the market cap are the controllers' instruments (the listed classes,
ADR 0014), so a per-share or cap-based ratio with a total numerator would mix
slices the other way.

## Decision

Both slices are read, carried, and published; each ratio pairs its numerator
and denominator **from the same slice**:

- `StandardizedFinancials` carries `net_income`/`equity` (controllers', as
  before) and `net_income_total`/`equity_total` (consolidated, as filed).
- The bare indicator names keep the controllers' basis: `roe`, `roa`,
  `net_margin`, `eps`, `bvps`, `pe`, `pb`, `payout`, `net_income`,
  `net_income_growth`.
- The whole-firm ratios gain a `_total` sibling on the consolidated basis:
  `roe_total` (total income / total equity), `roa_total` (total income /
  total assets), `net_margin_total` (total income / revenue), plus the
  headline `net_income_total`.
- Per-share and cap-based indicators get **no** `_total` variant — the count
  and the cap are controllers' instruments; a total numerator over them is a
  slice mismatch, not a second basis.
- The fidelity gate (#44) compares each platform cell against the column that
  matches the platform's own basis: its margins and ROE against the `_total`
  columns, its per-share figures against the controllers' ones.
- For a bank the DRE is the parent filing (ADR 0019), so its "total" is the
  parent bottom line — the figure the bank itself reports. The asymmetry is
  the filings', not ours.

## Consequences

- The ABEV3/VALE3/WEGE3/TOTS3 margin and ROE divergences stop being recorded
  `except`s: the platform's cells now compare clean against the `_total`
  columns, and the controllers' columns remain for the per-share pairing.
- Consumers must pick a slice knowingly — the API exposes both, and the
  front-end must label which one a screen shows (follow-up WEB issue).
- Four more columns to persist and migrate (0010); the fixture's exported
  inputs grow two fields.
- What this rules out: silently switching the bare names to the total basis
  (it would desynchronize `net_margin` from `eps` — the pairing the platforms
  themselves keep), and "fixing" a mixed-slice ratio by widening a tolerance.
- TOTS3's `net_margin` does **not** close even on the total basis
  (735,443 / 5,224,007 = 0.1408 vs its published 0.1496) — that residue is a
  different question and stays a recorded `except` under #116's successor
  investigation, not a reason to doubt the pairing rule.
