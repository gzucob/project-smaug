# 0054 — CPC 41 results come from the consolidated class disclosure

- **Status:** Accepted
- **Date:** 2026-08-11

## Context

The existing `eps` divided controller-attributable profit by the outstanding
share count at period end. CPC 41 instead requires the result attributable to a
class over its weighted-average shares in circulation, excluding treasury
shares. It also requires separate basic and diluted results, class-specific
economic rights, and retrospective adjustment for bonus issues, splits and
groupings.

The CVM DRE mirror already preserves the issuer's `3.99` disclosure: basic and
diluted values, with one leaf per reported class and source precision intact.
Those values are reais per share even when the rest of the statement is filed
in thousands of reais. Their code positions are not stable across issuers; the
leaf labels (`ON`, `PN`, `PNA`, `PNB`) are the class identity.

The available capital modules are closing snapshots. FRE carries approval-dated
capital rows and DFP/ITR composition carries closing treasury balances, but they
do not form a complete dated ledger of issuances, repurchases, cancellations and
potential ordinary shares. A weighted denominator reconstructed from those
snapshots would still guess when resources became receivable and when treasury
shares left or re-entered circulation.

CPC 41 also requires disclosures based on consolidated information when an
entity presents consolidated and separate statements. This conflicts with ADR
0019's bank-only choice of the parent DRE: a parent BACEN result cannot reconcile
to the consolidated class result filed under `3.99`.

## Decision

- Basic and diluted earnings per security come from the issuer's consolidated
  DRE `3.99` class leaves, without applying the statement currency scale.
- The security class comes from FCA instrument identity. The B3 class number
  distinguishes PN, PNA and PNB only after FCA has established that the security
  is a preferred share.
- A unit result is the sum of its FCA-declared component quantities multiplied by
  each component class result. An incomplete composition makes the result null.
- `eps` remains a compatibility alias of `eps_basic`; `eps_basic` and
  `eps_diluted` are the explicit public fields.
- The consolidated DRE is used for every consolidated analysis, including banks.
  Its controller-attributable result and its CPC 41 disclosure therefore share
  one filing lineage. This supersedes ADR 0019.
- No closing share count substitutes for a weighted average. When a reconciled
  class disclosure is absent, ambiguous, or cannot be composed into a TTM
  window, the result is null with a specific cause.
- A future reconstruction is eligible only when the mirror contains every dated
  capital and treasury movement and the economic terms of every potentially
  dilutive instrument. Differences between closing snapshots are not events.

## Consequences

- Issuances, repurchases, treasury shares, split-like retrospective adjustments,
  unequal class rights and convertible instruments are reflected exactly when
  the issuer reflected them in its basic/diluted disclosure.
- A disclosed zero remains zero. Parent or summary `3.99` rows do not fill an
  absent class leaf, and conflicting duplicate class leaves are rejected.
- TTM earnings per share is unavailable until a complete weighted-denominator
  lineage can be assembled; annual closed periods remain available from DFP.
- Bank profitability and valuation indicators move from the parent BACEN result
  to the consolidated controller result. They may no longer match press releases,
  but they reconcile to the regulatory basis required for consolidated CPC 41
  reporting.
- PNA/PNB results can be identified from the DRE even while issue #72 still keeps
  a multi-preferred-class market cap unavailable. The two questions no longer
  force one another into an approximation.
