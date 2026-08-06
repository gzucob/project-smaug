# 0050 — Primary-source invariants are the correctness gate

- **Status:** Accepted
- **Date:** 2026-08-06

## Context

The M1 gate compared calculator output with values copied from external
fundamental-data aggregators. Those values did not carry enough lineage to show
which CVM statement slice, accounting period, share base, price basis, or
corporate-action adjustment produced them. Agreement could therefore reward an
undocumented convention, while a result reconciled directly to CVM and B3 could
fail merely because it answered a different question.

The gate also privileged a curated portfolio plus a few representatives after
runtime identity resolution had already become exchange-wide. ADR 0046 kept that
external comparison as the arithmetic gate and added `smaug doctor --all` as a
coverage gate, leaving two conflicting source-of-truth models in force.

CVM is the project's filing source and B3 is its market-data source. They publish
the inputs from which the analysis is derived; aggregators publish derived outputs
without the lineage required to make them an authority.

## Decision

CI correctness gates derive only from:

- CVM and B3 fixtures whose primary-source provenance is explicit;
- formulas asserted directly from their declared inputs;
- accounting and market-data reconciliations; and
- domain invariants, including named-null behavior.

External aggregators may be used in disposable exploratory investigations. Their
published numbers are not committed fixtures, acceptance criteria, automated
gates, or authorities for selecting an accounting, period, share, dividend, or
price basis.

Regression coverage is selected by data characteristic rather than membership in
a curated ticker set. The characteristic suite covers the filed accounting regime,
parent and consolidated slices, period isolation, units, multiple listed classes,
treasury shares, corporate actions, and the separation of nominal, split-restated,
and dividend-adjusted price bases. Formula tests reconcile assets with liabilities
and equity, market capitalization class by class, margins with filed statement
lines, per-share values with outstanding shares, and dividend indicators with the
declared or paid basis they name.

`smaug doctor --all` remains the exchange-scale coverage gate. It proves that every
persisted null has a named cause; it does not prove that a non-null value is
arithmetically correct. Formula, reconciliation, and source-reader tests provide
that independent protection.

This decision supersedes ADR 0046 in full. It also supersedes only the
external-authority portions of earlier decisions, including ADRs 0001, 0003, 0005,
0010, 0015, 0018, 0019, 0022, 0024–0028, and 0032. Their formula, modelling, and
source decisions remain in force where supported by CVM/B3 evidence or an explicit
domain rationale; historical platform comparisons remain historical context.

## Consequences

- The external-value fixture, its tolerance and exception machinery, and its input
  export script are removed. The export mixed provenance after the price-source
  migration and could not qualify as a primary-source fixture without a field-level
  audit.
- Valuable cases previously colocated with that gate remain protected by focused
  tests: calculator formulas and statement slices, capital and market-cap
  reconciliation, TTM period isolation, price-basis separation, corporate-action
  restatement, and named-null propagation. The dividend-yield characteristic moves
  to the closed-year analysis test that controls both price bases directly.
- A ticker may still identify a CVM/B3-shaped fixture for traceability, and the
  personal portfolio remains a product preference. Neither makes the ticker an
  oracle or selects production behavior.
- Losing a broad table of copied outputs makes the primary-source tests more
  important. A new non-null mapping path needs a filed-line reconciliation; passing
  `doctor --all` alone is insufficient.
