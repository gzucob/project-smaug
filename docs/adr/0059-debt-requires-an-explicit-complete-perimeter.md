# 0059 — Debt requires an explicit complete perimeter

- **Status:** Superseded by [0063](0063-debt-instrument-perimeter-matrix.md)
- **Date:** 2026-08-11

## Context

ADR 0015 correctly stopped reading corporate code `2.01.04` as debt from an
insurance chart, where the same code means `Capitalização`. It then made the
opposite unsupported inference: because that chart had no generic borrowing
line, the calculator treated an insurance filer as having zero debt and
published `net_debt = -cash`. Absence proved only that the generic line was not
there, not that every borrowing, lease or subordinated instrument was zero.

The same fixed-code assumption was incomplete outside insurers. The CVM DFP
contains fixed and issuer-defined BPP lines. Many corporate filers leave the
fixed `2.01.04` / `2.02.01` lease children at zero and disclose a CPC 06 lease
liability under `Outras Obrigações`; others put loans and debentures there. A
sum of the two fixed codes silently omitted those explicit balances.

The 2025 primary fixtures show three distinct cases:

- BBSE3 opens its DRE with `Receitas das Atividades
  Seguradoras/Resseguradoras`. Its insurance BPP has no comprehensive current
  and non-current borrowing pair. That is incomplete coverage, not zero debt.
- PSSA3 opens with the corporate `Receita de Venda de Bens e/ou Serviços` and
  therefore stays on the corporate mapping. It files both fixed borrowing
  parents at zero, but also files material generic `Passivos financeiros` and
  separate `Passivo de Arrendamento` lines under `Outras Obrigações`. The lease
  is debt; the generic financial bucket cannot be classified completely from
  its label, so total debt remains unknown.
- CXSE3 also files corporately. Its standard borrowing parents explicitly carry
  its lease financing, so it follows the ordinary corporate rule with no ticker
  exception.

Insurance-contract, reinsurance, pension and capitalization liabilities are a
different economic perimeter. CPC 11 defines an insurance-contract liability as
the insurer's net contractual obligation under the insurance contract. It is a
product obligation, not borrowed financing. CPC 06, conversely, measures a lease
liability through interest and payments, so an explicitly filed lease liability
belongs to debt even when the issuer parks it outside the fixed debt parents.

## Decision

- `total_debt` is the sum of the comprehensive current and non-current
  `Empréstimos e Financiamentos` aggregates plus explicitly named debt balances
  outside them: loans, financing, debentures, lease liabilities, subordinated
  obligations and named debt instruments. The shallowest matching line is used
  so a parent and its breakdown are never double-counted.
- Both maturity aggregates must be present with numeric values. A published zero
  is evidence; a missing aggregate is not. Debt zero is published only when both
  aggregates and every separately named debt balance are explicitly zero.
- A non-zero undecomposed `Passivo(s) financeiro(s)` bucket makes coverage
  incomplete. It is neither added wholesale nor ignored: the calculator returns
  a named `INCOMPLETE_DEBT_COVERAGE` null until the structured filing identifies
  its economic components. Derivatives and option liabilities are not debt.
- Insurance-contract/reserve, reinsurance, pension, third-party product deposit
  and capitalization liabilities are not promoted to financing debt from their
  balance-sheet location. Each remains part of total liabilities, outside this
  net-debt bridge.
- The rule applies to every non-bank BPP. It is keyed on filed labels and
  maturity sections, not on a ticker. Banks remain `INAPPLICABLE_REGIME` because
  deposit funding is their operation and cannot be converted into corporate net
  debt.
- Net debt, gross-debt leverage, enterprise value, EV multiples and statutory
  ROIC all require this same complete debt input. No dependent formula may fall
  back to zero. Analysis rows record the basis as
  `debt_basis = cvm_bpp_explicit_interest_bearing`.
- Migration 0019 invalidates every persisted value built on the former
  unproved perimeter. The raw CVM mirror is unchanged; `smaug analyze` rebuilds
  the reproducible PostgreSQL rows after the formula series is complete.

## Consequences

- BBSE3 no longer publishes `-cash`, an understated EV or an EV/EBIT derived
  from an absent debt line.
- PSSA3 no longer publishes net cash merely because its fixed borrowing parents
  are zero while a material generic financial-liability balance remains
  undecomposed.
- A genuine zero-debt filer can still publish net cash, but the zero is now a
  filed fact. Explicit loans, leases and subordinated instruments compute under
  the same formula for a corporate or insurance DRE.
- Corporate filers whose lease or borrowing liabilities sit under
  `Outras Obrigações` stop understating total and net debt. This general fix is
  made together with the insurer correction so one exchange-wide recomputation
  is sufficient.
- The insurer-underwriting indicators in #98 are unaffected. Technical reserves
  remain available as filed liabilities; this ADR does not turn them into debt
  or introduce loss/combined-ratio formulas.

## Primary sources

- [CVM — DFP open dataset, including fixed and non-fixed BPA/BPP lines](https://dados.cvm.gov.br/dataset/cia_aberta-doc-dfp)
- [CPC 06 (R2) — lease-liability interest and payment measurement](https://www.cpc.org.br/Arquivos/Documentos/533_CPC_06_%28R2%29.pdf)
- [CPC 11 — insurance-contract liability definition](https://www.cpc.org.br/Arquivos/Documentos/215_CPC_11_rev%2020.pdf)
