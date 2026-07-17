# 0028 — The composition series detects a split the FRE year hides

- **Status:** Accepted
- **Date:** 2026-07-17
- **Supersedes:** [0027](0027-per-share-history-is-split-adjusted.md)

## Context

ADR 0027 restates closed-year share counts onto the current base by reading a
clean rational ratio between consecutive **FRE** years — a corporate action on
the whole base (split, grupamento, bonificação) multiplies the count by a small
rational, exact to the share, while a real issuance or a buyback cancellation is
dirty and is left alone. It closed the discontinuity in the LPA/VPA series and
put the count on the same base as Yahoo's split-adjusted price.

ADR 0027 recorded one accepted miss: a **composite action in a single FRE year**.
VIVT3's 2:1 split and a same-year cancellation appear in the FRE only as one
combined ratio (1,652,588,360 → 3,261,287,392 = 1.9734), which is dirty, so
nothing restated and VIVT3's ≤2023 per-share history kept the pre-split base —
paired with a price series Yahoo had already halved for the split.

The raw mirror carries a second, better-dated source. The FRE files a year's
capital **as of its approval date**, so an early-year approval drags a later
event into the wrong year (VIVT3's 2024 FRE, approved 2025-04-15, already reports
the post-split count). The statements' `composicao_capital` member is dated by the
**real quarter** instead, and it is filed quarterly: VIVT3's split shows there as
a clean ×2 between 2025-Q1 (1,630,643,696) and 2025-Q2 (3,261,287,392) — exact to
the share, separate from the cancellations that surround it.

Two hazards kept ADR 0027 from using that member, and both are now handled rather
than avoided:

- **Scale.** The member is filed at the issuer's own scale with no column saying
  which (ADR 0017). A thousands-scale row is rounded, so its ratios cannot be
  exact to the share — LREN3's buyback rounds to a false clean 19/20. Only
  **units-scale** rows (reconciled against that year's FRE total) are trusted.
- **Internal inconsistency.** A row's count need not agree with the FRE year that
  frames it. The detection never assumes agreement: it anchors on the FRE's
  post-jump count and only *confirms* the split's magnitude from the member.

## Decision

The restatement of ADR 0027 stands unchanged, with one addition. When a
consecutive FRE-year ratio is dirty **and share-increasing**, it is retried
against the composition's **units-scale, quarter-by-quarter** series:

- A consecutive composition pair whose ratio is a clean rational **greater than 1**
  and whose post-action total matches the FRE year's post-jump count (within a
  tight 0.5%) is the split; its clean ratio is the restatement factor.
- The composition is consulted **only** for a dirty, share-increasing FRE ratio.
  A clean FRE ratio (BBAS3's bonus, LREN3's bonificação, SANEPAR, HAPVIDA's
  grupamento) is handled exactly as in ADR 0027, and a share-*decreasing* ratio (a
  cancellation) is never restated — so a clean ×2 elsewhere in the series cannot
  pull a buyback into a restatement.
- The match must be **unique**: an ambiguous series (two candidate splits landing
  on the same count) restates nothing, keeping the ADR 0027 behaviour.

Detection stays pure and derivable: the mirror keeps every filing, the factor is
recomputed on every read and never stored.

## Consequences

- VIVT3's ≤2023 per-share history lands on the post-split base; its LPA/VPA series
  is continuous (VPA 20.85 → 20.56 → 21.05 → 21.41 → 21.51 across 2021–2025, where
  it had stepped 42 → 21 at the 2023/2024 boundary) and pairs with the adjusted
  price. The pinned `test_a_composite_action_in_one_year_is_missed_and_documented`
  is retired for `test_a_composite_action_is_restated_via_the_composition`.
- The existing FRE-only detections are untouched: they never reach the composition
  path, confirmed by the fidelity gate and the capital unit tests.
- What this costs: a second source in the detection path, and a trust in the
  units-scale composition being exact to the share for the pair that matches. The
  units restriction is the guard; a thousands-only filer contributes an empty
  series and simply falls back to ADR 0027's FRE-only behaviour.
- Residual risk, unchanged from ADR 0027 and now also on the composition side: a
  real issuance of an exactly clean proportion, to the share, on the anchored
  count would be misread as a split. Nothing filed distinguishes the two; the
  fidelity gate remains the backstop.
