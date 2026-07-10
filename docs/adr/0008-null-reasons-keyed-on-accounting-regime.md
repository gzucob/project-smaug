# 0008 — Null reasons are a parallel map keyed on the accounting regime

- **Status:** Accepted
- **Date:** 2026-07-10

## Context

A bank's ticker page shows ~37 `n/d` cells, and nothing in the domain, the API
or the UI distinguishes their causes (#30). At least four exist: the indicator
is economically meaningless under the filer's accounting regime; our mapper
deliberately never reads the account; an upstream input (price, share count,
prior year) is missing; or the company files under a regime other than the one
its sector predicts — CXSE3, an insurer by sector, files as a holding
(ADR 0006). Both planned consumers need the cause to be *enumerable*:
`smaug doctor` (#47) reports "a value, or a null with a named cause", and the
reference-platform tolerance test (#44) must tell "the platform has no value
either" apart from "we failed to map the account".

Two constraints shaped the shape of the answer:

- The `Indicators` contract already rules out sentinel values inside its
  `Decimal | None` fields — a sentinel would poison every consumer's
  arithmetic.
- The `Sector` enum does not predict how a company files. The real filings
  show the regime is a property of the statement itself: banks put equity at
  `2.07` and open the DRE with "Receitas de Intermediação Financeira", insurers
  open with "Receitas das Atividades Seguradoras/Resseguradoras", and the
  corporate schema opens with "Receita de Venda de Bens e/ou Serviços" — which
  is how CXSE3 actually files, despite its sector.

## Decision

- **A `NullReason` enum of seven values grouping the four root causes.**
  `inapplicable_regime`, `source_account_unmapped`, `source_account_absent`
  (the filing has no such line — distinct from "we never looked"), the
  upstream family split per input (`missing_price`, `missing_share_count`,
  `missing_prior_period`) so a report can say *which* input, and
  `unexpected_regime`.

- **The reason is attributed where the null is born, and travels with the
  data.** The CVM mapper records what it saw (`filed_regime`, detected off the
  DRE's 3.01 label, and `unmapped_fields`, the lines it deliberately skipped)
  on `StandardizedFinancials`; the pure calculator classifies every null it
  produces with a documented precedence (regime guard → unmapped → absent →
  market inputs → prior period); the result is persisted as one JSON map
  beside the indicator columns and mirrored by the API. Nothing reconstructs
  causes after the fact.

- **The vocabulary keys on `AccountingRegime` (bank / insurance / corporate),
  never on the `Sector` enum.** A detected regime that differs from the one
  the sector predicts makes every regime-driven null `unexpected_regime`
  instead of `inapplicable_regime` or `source_account_unmapped`. An
  undetectable regime is never guessed.

- **A null with no recorded reason is "unclassified"** — a first-class,
  derivable status (e.g. a zero denominator), not a stored enum member.

## Consequences

- #47 can report a named status for every cell, and #42-shaped price losses
  surface as `missing_price` rather than a bare null. #44 can set tolerances
  only where both sides have a value.
- Rows persisted before the vocabulary (NULL column) degrade to unclassified;
  re-running `analyze` back-fills them. No behaviour changed — indicator
  values are byte-identical before and after; only attribution was added.
- The `_NEEDS` table in `calculator.py` must stay in lockstep with
  `compute()`: a new indicator without its row silently yields unclassified
  nulls. The calculator tests pin the mapping for each cause.
- Which `inapplicable_regime` guards are *truly* meaningless (vs merely
  unconventional but computable) remains unaudited against the reference
  platforms — this ADR standardizes the vocabulary, not the verdicts. The
  audit stays open in #30, feeding on #44's fixture.
- Regime detection covers the three schemas present in the portfolio. A new
  filer with a fourth schema stays undetected until a marker is added — safe
  (no mismatch is flagged without positive evidence), but blind.
