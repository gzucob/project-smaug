# 0061 — Insurer ratios require separated underwriting components

- **Status:** Accepted
- **Date:** 2026-08-13

## Context

ADR 0021 deferred the insurer indicator set because the two insurer-sector
portfolio companies did not underwrite in their own filings. The exchange-scale
universe now includes IRB Brasil Resseguros, whose 2022 consolidated CVM DFP
files the required underwriting components separately:

- `3.01.02.01` earned reinsurance premiums: R$ 7,021,200 thousand;
- `3.02.02.01` retained reinsurance claims: R$ -6,911,514 thousand;
- `3.02.02.02` reinsurance acquisition expenses: R$ -255,606 thousand;
- `3.04` administrative expenses: R$ -421,237 thousand.

The current structured chart is not equivalent. From the IFRS 17 transition it
publishes broad insurance-service and reinsurance aggregates under `3.02`, but
does not preserve claims and acquisition costs as separate structured lines.
Calling the aggregate “claims” would change both the loss-ratio numerator and
the combined-ratio perimeter while keeping the old labels.

## Decision

- Add two insurance-regime indicators, expressed as fractions:
  - `loss_ratio` = -claims incurred / earned premiums;
  - `combined_ratio` = -(claims incurred + acquisition costs + administrative
    expenses) / earned premiums.
- Inputs must come from the same CVM DRE period and consolidation slice. The
  mapper sums the insurance and reinsurance branches where both exist:
  `3.01.01.01` + `3.01.02.01`, `3.02.01.01` + `3.02.02.01`, and
  `3.02.01.02` + `3.02.02.02`; administrative expenses use `3.04`.
- Filed expenses stay signed in `StandardizedFinancials`. Only the calculator
  reverses the expense sign once, so a normal negative expense becomes a positive
  ratio numerator while a positive reversal remains a credit rather than being
  turned into a cost. The stored statement facts preserve the CVM convention.
- The IFRS 17 `3.01.01`/`3.01.02` revenue aggregates and
  `3.02.01`/`3.02.02` expense aggregates do not substitute for the separated
  leaves. When a required component is absent, the ratio is null with
  `SOURCE_ACCOUNT_ABSENT`; a filed zero premium produces `ZERO_DENOMINATOR`.
- Both indicators are inapplicable outside an insurance filing regime. Sector is
  not the key: an insurer-sector holding that files the corporate chart receives
  `INAPPLICABLE_REGIME` (ADR 0020).
- TTM sums the four statement flows only when every contributing quarter carries
  the component. Migration 0020 adds the two derived columns; existing rows stay
  null until `smaug analyze` rebuilds them.
- Primary-source CVM inputs and formula invariants are the correctness gate. An
  external aggregator value is not a fixture or acceptance authority (ADR 0050).

## Consequences

- IRB's 2022 filing yields a 98.4% loss ratio and a 108.1% combined ratio under
  the declared formula, with the exact source accounts protected by tests.
- Current IFRS 17 periods remain named nulls until a public, reproducible source
  supplies the separated components on one period and consolidation perimeter.
  The broader structured expense subtotal is never relabelled to improve
  coverage.
- BBSE3's historical zero underwriting lines remain values rather than missing
  data, while CXSE3's corporate filing remains outside the insurer-ratio regime.

## Primary sources

- [CVM — 2022 annual filing bulk archive](https://dados.cvm.gov.br/dados/CIA_ABERTA/DOC/DFP/DADOS/dfp_cia_aberta_2022.zip)
- [CVM — 2025 annual filing bulk archive](https://dados.cvm.gov.br/dados/CIA_ABERTA/DOC/DFP/DADOS/dfp_cia_aberta_2025.zip)
