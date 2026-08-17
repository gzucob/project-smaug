# 0063 — Debt uses an explicit instrument-perimeter matrix

- **Status:** Accepted
- **Date:** 2026-08-17

## Context

ADR 0059 established the complete current/non-current borrowing pair as the
minimum evidence for corporate and insurance debt. It also named the initial
set of separately disclosed financing balances, but the derived result did not
retain the BPP line-by-line decision. That made a null or an included balance
difficult to audit and left labels such as mutuos, securitization, acquisition
debt and CCI without a stable disposition.

The same BPP hierarchy can contain a comprehensive parent and its detail rows,
or liabilities that are product obligations rather than financing. A sum that
mixes parent and child rows, or promotes a generic financial-liability bucket,
changes the economic perimeter without evidence.

## Decision

- Keep the complete current and non-current aggregate requirement from ADR
  0059. A missing or unreadable aggregate remains
  `INCOMPLETE_DEBT_COVERAGE`; it is never converted to zero.
- Classify each relevant BPP line in a persisted matrix with its code, label,
  hierarchy role, value, instrument family and disposition: `included`,
  `excluded` or `ambiguous`.
- Treat explicitly named loans, financing, debentures, subordinated
  instruments, leases, mutuos, securitization/receivables, and acquisition
  debt/CCI as candidate financing instruments only when the line itself is
  outside a selected aggregate and the complete aggregate evidence exists.
  This is a label-and-hierarchy rule, not a ticker or sector proxy.
- Select the shallowest parent for an aggregate or named instrument. Preserve
  descendants as excluded `CHILD_DETAIL_DOUBLE_COUNT` evidence; never add both
  a parent and its detail rows.
- Keep generic financial liabilities ambiguous when their economic components
  are not named. Keep insurance, reinsurance, technical-reserve, pension,
  capitalization and derivative liabilities outside financing debt with an
  explicit exclusion reason.
- Banks remain inapplicable for corporate debt metrics. The matrix records that
  regime blocker rather than interpreting deposit funding as corporate debt.
- Carry the primary root blocker and secondary reasons through TTM, closed-year
  analysis, PostgreSQL and the read API. The persisted `debt_basis` remains
  `cvm_bpp_explicit_interest_bearing`.

## Consequences

- Every persisted debt decision can be reconciled to the source BPP lines and
  its issuer, regime, period and analysis view.
- Explicitly named instruments can be counted once without losing the evidence
  that a parent/detail row was rejected.
- Generic or product liabilities remain visible to investigation without being
  silently promoted to debt; incomplete coverage continues to block EV and
  leverage formulas.
- The matrix adds storage and API payload size, and legacy rows remain marked
  until `smaug analyze` recomputes them.

## Primary source boundary

The CVM BPP mirror is the only source for the line, hierarchy and filed value.
The matrix does not infer an instrument from price, market capitalization,
sector, ticker suffix or an external aggregator.
