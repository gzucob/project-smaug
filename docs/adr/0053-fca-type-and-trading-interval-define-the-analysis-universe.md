# 0053 — FCA type and trading interval define the analysis universe

- **Status:** Accepted
- **Date:** 2026-08-11

## Context

A B3 code's numeric suffix is not an instrument identity. The current FCA
archive uses suffix 11 both for units and for subscription warrants, and it keeps
codes whose trading interval has ended. Filtering only by ticker shape therefore
puts warrants and terminated securities into exchange-wide fundamental analysis.

Unit composition has the opposite problem. The FCA identifies current units
reliably through `Valor_Mobiliario`, but most rows describe their bundle as
textual ON/PN quantities rather than component tickers. Reading only explicit
ticker components leaves the unit count absent and lets per-security indicators
fall back to the company's underlying share total.

## Decision

- Every `CompanyIdentity` carries the instrument kind derived from the FCA's
  `Valor_Mobiliario`, the original label, and the end of its trading interval.
- The current fundamental universe contains only still-trading ordinary shares,
  preferred shares and units. Ticker shape remains syntax validation, not proof
  of instrument kind or current listing.
- A known but excluded code remains resolvable for on-demand diagnosis. The
  rejection names its FCA instrument label or trading end date.
- Unit identity comes only from the resolved FCA kind. A suffix-11 warrant is not
  a unit in any downstream price or per-security calculation.
- Unit composition accepts complete FCA descriptions made from explicit
  component tickers or textual ON/PN quantities. A partially understood bundle
  is rejected as unreadable rather than summed incompletely.
- A true unit with no readable composition produces
  `MISSING_UNIT_COMPOSITION` for per-security indicators. It never falls back to
  the total underlying share count.

This supersedes ADR 0025's suffix-based unit assumption and its restriction of
composition parsing to explicit component tickers. Its class-by-class market-cap
decision remains in force.

## Consequences

- Current units resolve from the regulator's declared security type even when
  their bundle is written only as ON/PN text.
- Warrants, receipts and terminated codes no longer expand `analyze --all` or
  `doctor --all`, while an explicit request receives an actionable explanation.
- Instrument-kind consumers require the FCA identity map at composition time;
  there is no context-free `ticker.endswith("11")` shortcut.
- New FCA wording can make a unit's per-security indicators null until the
  grammar is extended, which is safer than publishing a denominator inferred
  from a partial reading.
