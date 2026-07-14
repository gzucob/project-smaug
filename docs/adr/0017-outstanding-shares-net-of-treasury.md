# 0017 — Shares are counted net of treasury, at a scale derived from the FRE

- **Status:** Accepted
- **Date:** 2026-07-14

## Context

The share counts come from CVM's FRE (ADR 0004) and are the shares a company has
**issued**. Some of those shares the company has bought back and holds itself: they
are issued but **not outstanding** — they draw no dividend and carry no claim on
earnings. Pricing them into the market cap (ADR 0014) over-values the company, and
dividing by them understates every per-share indicator. The error is small but
systematically in one direction: 0.4% for BBAS3, 3% for BBSE3, and 6% for VALE3.

Treasury shares are filed in exactly one place: the statements' `composicao_capital`
member, mirrored as the `CAPITAL_DFP` module (ADR 0016). The FRE does not carry them.

That member has a defect the FRE does not: its counts are filed **at the filer's own
scale, and the member has no column saying which**. TAEE11, VALE3 and CXSE3 file
thousands; PETR4, BBAS3 and WEGE3 file units; BBDC4 filed thousands through 2024 and
units from 2025. A treasury figure subtracted at the wrong scale is wrong by 1000x —
an error three orders of magnitude larger than the one it set out to correct.

## Decision

The share counts served to the analysis are **outstanding**: issued, less treasury.
Both the market cap and the per-share denominators (EPS/BVPS) read them, so `price ÷
EPS` and `cap ÷ net income` keep answering the same question.

The scale of a `CAPITAL_DFP` filing is **derived, never assumed**. The member files
its own issued total, which is the same quantity the FRE reports for that year, so the
ratio between the two totals is the multiple: nearer to 1 than to 1000 (their geometric
midpoint, √1000) is units, otherwise thousands. The chosen scale must then reconcile
the two totals to within 10x — the two filings are months apart and one may predate a
split, so an exact match is not on offer, but a 10x gap means the two are not
describing the same company's shares.

A composition that cannot be read — no filing for the year, a scale that will not
reconcile, a negative treasury count (BBDC4 files one for 2022), or a treasury stake
that swallows its own class — yields **no adjustment**: the issued count is served as
it stands, and the approximation is logged. Keeping an over-count of a few percent is
the lesser error, and a logged approximation is not a silent one.

## Consequences

- The cap and the per-share indicators move together and stay mutually consistent; a
  reader comparing `price ÷ EPS` against P/E gets the same number.
- Every ticker's numbers change slightly, and VALE3's by ~6% — the reference-platform
  fixture (#44) must be pinned *after* this, not before.
- The scale is established per *filing*, not per filer, so a company that changes how
  it files (BBDC4 did) is handled without a per-ticker table to maintain.
- The reconciliation depends on the FRE total being right, which is what #86 had to fix
  first. A ticker with no FRE filing for any year gets no share count at all — that is
  unchanged, and the treasury reading cannot rescue it.
- We accept a known over-count where the composition is unreadable (BBDC4 2022, ~0.2%)
  rather than a null: the cap and every multiple built on it would otherwise disappear
  over a filing defect worth a fifth of a percent.
