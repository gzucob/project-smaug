# 0015 — The CVM mapping keys on the filed accounting regime, not the sector

- **Status:** Accepted
- **Date:** 2026-07-13

## Context

ADR 0010 split a financial filer's nulls into *genuinely inapplicable* and
*merely unmapped*, and left the unmapped half to #48: 174 of the 454 named nulls
in `smaug doctor` — the single largest block — were `source_account_unmapped`.

The premise was that those accounts were missing from our mapping. Reading the
raw mirror for the four financial tickers showed something narrower and worse:
**the accounts are there, and `standardize()` never looked at them.** It chose
what to read from `sector.is_financial`, taking an early return that skipped ten
fields for any bank or insurer. But the sector does not determine the schema —
what the filer *files* does, and the two disagree across the portfolio:

| Ticker | Sector | Balance sheet as actually filed |
|---|---|---|
| BBAS3, BBDC4 | bank | bank — no current/non-current split, no debt line |
| BBSE3 | insurer | insurer DRE, **corporate-shaped balance sheet** |
| CXSE3 | insurer | **corporate outright** (3.01 "Receita de Venda") |

So BBSE3's current assets (1.01) and CXSE3's entire corporate chart of accounts
were being skipped as "financial", while both were sitting in Mongo, filed.

Reading the mirror also turned up a second instance of ADR 0005's dead needle —
**the same code means different things under different regimes**, and a
code-based read outside its regime yields a plausible wrong number rather than a
null:

- `3.05` is EBIT (before financial result) for a corporate filer, but **profit
  before tax** for a bank; an insurer's EBIT is at `3.07`.
- `2.01.04` is "Empréstimos e Financiamentos" for a corporate filer, but
  **"Capitalização"** for an insurer.

## Decision

`standardize()` dispatches on the **accounting regime the filer actually filed
under** (detected from the DRE's 3.01 label, ADR 0008), falling back to the
regime its sector predicts only when the DRE is absent or unrecognized. Each
regime has its own branch, and a CVM code is only ever read inside the branch
that owns it.

`_INAPPLICABLE_BY_REGIME` in `calculator.py` becomes the single source of truth
for what a regime cannot support: it now drives **both** the suppressed value and
the null's reason. The blanket `is_financial` value-guard in `compute()` is
deleted — with the mapper reading each regime's own accounts, the ratios a
financial filer supports must compute, and the ones it does not are named once,
in that map, instead of in a parallel hand-maintained list.

Three verdicts ADR 0010 had explicitly deferred to #48 are settled, with the
portfolio owner:

- **A bank's current ratio and P/working-capital are `inapplicable_regime`**, not
  unmapped. Its balance sheet has no current/non-current split at all — the two
  reference platforms disagree tenfold on the number (AUVP ~10, Investidor10 ~1),
  which is what extrapolating from a generic template looks like. This reverses
  ADR 0010's "if any reference computes it, treat it as computable" for this one
  case: the schema, not the platform, is the authority on what exists.
- **A bank's ROIC is `inapplicable_regime`.** Its denominator is equity + net
  debt, and net debt is already inapplicable for a bank (a deposit is funding,
  not borrowing) — lighting up ROIC would mean inventing a bank "debt".
- **A bank's `ebit` carries 3.05, its profit before tax**, and its `gross_profit`
  carries 3.03, its net interest income. Both are deliberate approximations: a
  bank has no line to strip because interest *is* its operation. This is what the
  reference platforms compute, and it is what makes a bank's margins and P/EBIT
  meaningful rather than blank.

The bank and insurer line items #27 needs are mapped, signed as filed (the CVM
records an expense negative and the mirror does not flip it): `interest_income`
(3.01.01), `interest_expense` (3.02.01), `loan_loss_provision` (3.04.01),
`fee_income` (3.04.02); `earned_premium` (3.01.01) and `claims_incurred`
(3.02.01) for an insurer.

## Consequences

- `source_account_unmapped` goes **174 → 0**. 138 cells that were blank now carry
  a number, and `unclassified` stays 0 (the M0 gate holds). Every remaining null
  for a financial filer is inapplicable, absent at the source, or an upstream
  input — never "we did not look".
- **The insurer schema has no borrowings line whatsoever**, so an insurer's net
  debt, debt/equity, EV/EBITDA and ROIC are `source_account_absent`: we looked,
  and there is nothing to read. This is the finding #48 required to be recorded
  rather than worked around — 2.01.04 is "Capitalização" there, and 2.02.01 is
  payables and provisions. It contradicts ADR 0010's expectation that these were
  merely unmapped for an insurer.
- **D&A stays unmapped for both financial regimes**, and is the only field left in
  `unmapped_fields`. A bank files it inside a filer-specific "Outras Despesas
  Operacionais" breakdown whose sub-codes are not stable across banks, and no
  indicator consumes it — EBITDA is inapplicable under both regimes.
- A bank's **FCF now computes** (CFO − capex), because ADR 0010's audit found it
  computable and nothing in the schema forbids it. It is volatile by nature: a
  bank's operating cash flow is dominated by the loan book and deposit flows
  (BBAS3 +151.9bn, BBDC4 −74.2bn for 2025). Whether it is *useful* enough to
  surface is a modelling question for #27, not a mapping one.
- The regime branches are pinned by `test_mongo_fundamentals.py` against the real
  codes and labels in the mirror — including a test that an insurer's EBIT is read
  at 3.07 and not 3.05, and that its 2.01.04 is not summed into debt. ADR 0005's
  lesson stands: test the match, not just the result.
- **#27 is unblocked** — the bank/insurer indicator set now has its inputs.
