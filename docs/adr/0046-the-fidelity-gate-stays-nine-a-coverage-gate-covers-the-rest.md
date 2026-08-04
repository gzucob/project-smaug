# 0046 — The fidelity gate stays nine; a coverage gate covers the rest

- **Status:** Accepted
- **Date:** 2026-08-04

## Context

`test_reference_fidelity` (#44) compares our indicators against a
hand-verified reference platform snapshot for nine tickers plus five sector
representatives. #169 named the gap: since #109/M2, the mirror holds 506
traded codes, and the fixture still covers 14 of them — 2.8% of what
`smaug analyze --all` runs. A mapping bug that misses every one of those 14
regimes' edge cases has 492 places to hide, and the M2 acceptance criterion
("without regressing the nine's fidelity test") never asked the exchange-scale
question at all.

Two shapes were on the table: widen the fidelity fixture per accounting
regime, or add a second, cheaper gate over the batch. Widening the fixture
means hand-verifying a new reference value per cell per company (ADR 0022 —
never a platform read-off taken on faith) — real work, unbounded by how many
regimes eventually need a representative.

## Decision

**The fidelity gate stays at nine plus the five representatives. A second
gate, `smaug doctor`, now covers the other 492 by exiting non-zero on any
*unclassified* null** — a cell with no value and no `NullReason` (ADR 0008)
attributed to it.

The two ask different questions and neither substitutes for the other:

* **Fidelity** asks "is the arithmetic right", against numbers a human checked
  against a primary source. It can only ever cover as many cells as someone
  has verified by hand, so it stays small on purpose.
* **Coverage** asks "does every cell know why it is what it is" — a much
  weaker claim, but one `doctor` can already make about all 316,008 persisted
  cells, because every null already carries a cause that was named at the
  moment the calculator produced it. An unclassified null is not evidence of a
  wrong number; it is evidence of a **cause nothing has named yet** — which is
  exactly the shape a new regime's edge case takes before anyone has looked at
  it.

The threshold is **zero, not a share**. #169's own framing floated "no
unclassified nulls above some share of cells", but a share forgives exactly
the failure this gate exists to catch: a mapping bug that is rare across the
whole exchange but total within the one regime it hits would round to noise
against 316,008 cells and pass anyway. Zero has no such blind spot, and
today's mirror already clears it — `smaug doctor --all` reports 0 unclassified
across every persisted cell, so the gate starts green, not aspirational.

## Consequences

`smaug doctor` changes from a report to a gate: it now returns exit code 1
when `DoctorReport.unclassified > 0`, same convention `analyze` already uses
for a failed run. Every existing call site printed the unclassified count
already (`format_doctor`/`format_doctor_summary`); nothing about the report's
shape changes, only what the process does with it.

**What this does not cover**: an indicator that is wrong but *not null* — a
mapping bug that reads the right shape from the wrong account produces a
value, and a value is not what `doctor` inspects. That risk is unchanged by
this decision and stays where ADR 0022's buckets and the nine's hand-verified
numbers already sit.

**Widening the fixture remains available later**, at the cost this ADR
declined to pay now: each new cell needs a primary source, not a platform
read-off. Nothing here forecloses it — the two gates are additive, not a
choice between them going forward.
