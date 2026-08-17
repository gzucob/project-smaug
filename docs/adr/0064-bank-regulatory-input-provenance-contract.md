# 0064 — Bank regulatory inputs require a persisted source contract

- **Status:** Accepted
- **Date:** 2026-08-17

## Context

ADR 0058 keeps bank net-interest margin, efficiency, and cost-of-risk separate
from CVM closing balances. The six optional inputs already exist in the domain,
but a populated value previously had no proof of where it came from, which
period it covered, or which perimeter and averaging rule it used. The TTM
builder also dropped those inputs, and persisted rows could not explain a
published bank ratio's basis.

## Decision

- A bank ratio is eligible only when both members of its declared pair are
  present in one `BankRegulatoryProvenance` contract:
  - annualized interest result + average earning assets;
  - complete efficiency expenses + complete efficiency income;
  - annualized credit-loss expense + average credit portfolio.
- The contract must carry source, period start/end, perimeter, averaging
  methodology, and basis. A closing balance or a partial CVM subtotal is never
  accepted as an implicit substitute.
- Missing provider data remains `MISSING_REGULATORY_DISCLOSURE`; a one-sided
  pair is `PARTIAL_REGULATORY_DISCLOSURE`; incomplete or incompatible metadata
  is `INCOMPATIBLE_REGULATORY_DISCLOSURE`.
- Valid inputs and their contract travel through TTM, calculation, PostgreSQL,
  and the read API. The contract is one disclosure lineage, so the two inputs
  cannot silently come from different periods, perimeters, or bases.
- Bank EBIT, EV/EBIT, and corporate FCF remain inapplicable. Nonbanks never
  receive bank ratios merely because a field happens to be populated.

## Consequences

The product can publish a bank ratio only with an auditable source basis. The
current CVM-only mapper still produces named nulls because no regulatory or
issuer provider is wired; enabling a future provider requires populating the
contract rather than bypassing it. Partial disclosures remain visible and
actionable without being confused with an inapplicable regime.

## Source boundary

The future provider must be an official regulator or issuer disclosure. Its
raw source, period, perimeter, average-balance method, and basis become part of
the persisted contract. External screeners and CVM closing balances are not
accepted as substitutes.
