# 0021 — A bank gets three ratios of its own, read from the parent chart of accounts

- **Status:** Accepted
- **Date:** 2026-07-14

## Context

A bank nulls eight of our indicators, and rightly so (ADR 0010): a deposit is funding,
not borrowing, so net debt, EV/EBITDA and the leverage ratios are meaningless for it.
Nothing was offered in their place — the screen said what a bank *is not*, and nothing
about what it is.

The three ratios that describe a bank are not exotic: how wide the spread it earns is,
how much of that spread its own payroll consumes, and what its lending is costing it in
defaults. They are what the banks themselves report, quarter after quarter.

Two obstacles stood in the way, and both are properties of the filings:

- **The chart of accounts is not the one #48 mapped.** ADR 0019 takes a bank's income
  statement from its **parent** filing, whose BACEN chart differs from the consolidated
  IFRS one: the loan-loss provision is deducted *inside* the intermediation expenses
  (so 3.03, our `gross_profit`, is already **net** of it), where the consolidated chart
  puts it below at 3.04.01. The lines mapped for the consolidated chart therefore read
  the wrong cells — the provision came back as zero.
- **The two banks disagree on the codes.** The provision is 3.02.05 for BBAS3 and
  3.02.04 for BBDC4.

## Decision

Three bank-only indicators, computed from lines read **by label, scoped to their parent
account** — never by code, which the two banks do not agree on:

- **Net interest margin** = (`gross_profit` − `loan_loss_provision`) / total assets. The
  provision is filed negative, so subtracting it *adds the spread back*, recovering the
  *margem financeira bruta* the banks report before writing anything off.
- **Efficiency ratio** = (personnel + administrative expenses) / (interest margin + fee
  income). A cost, so the sign is flipped to read as one.
- **Cost of risk** = loan-loss provision / loan book, where the loan book is the credit
  portfolio net of the balance-sheet provision carried against it (only BBDC4 fills that
  line in; BBAS3 files its portfolio already net).

All three are **inapplicable under every other regime** — a company that sells goods has
no spread, no loan book and no payroll measured against a spread — so they null with
`INAPPLICABLE_REGIME`, not with a missing input.

The mismapped `interest_income` / `interest_expense` fields are deleted rather than
re-pointed: under the parent chart 3.01 *is* the revenue and 3.02 is not a clean interest
cost, so neither field had a faithful reading left.

## Consequences

- The numbers land where the banks publish them: BBAS3's efficiency reads 31.1% for 2024
  and 33.9% for 2025 (it reports ~31–34%); BBDC4's reads 47.2% and 46.1% (it reports
  ~45–50%). The cost of risk tells the story of the last three years without a word —
  BBAS3 3.56% → 4.09% → **5.79%** as the agricultural book deteriorated, BBDC4 5.53% →
  4.26% → **4.12%** as its own recovered.
- The margin divides by *total* assets rather than by earning assets, which CVM's
  structured statements do not separate. It therefore sits below what a bank publishes.
  Recorded as a caveat on the indicator, not hidden.
- `smaug doctor` grows by 162 cells (three indicators × 54 exercises), 126 of them
  inapplicable and none unclassified.
- The **insurer** set (loss ratio, combined ratio) is *not* built here, and #98 says why:
  neither insurer in the portfolio underwrites anything. BBSE3 files 0.00 on every
  insurance line — it holds insurers, it is not one — and CXSE3 files as a corporate
  holding (ADR 0006). The formulas would compute correctly and return null for both.
