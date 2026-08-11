# 0058 — Bank ratios require explicit average bases and one disclosed perimeter

- **Status:** Accepted
- **Date:** 2026-08-11

## Context

ADR 0054 moved bank analysis back to the consolidated DRE so controller profit
and the filed CPC 41 class result share one lineage. Three other bank decisions
still mixed concepts that the CVM structured statements cannot reconcile:

- `3.05` was stored as `ebit`, although the bank chart names it profit before
  tax. Interest is the operation, so that line is not earnings before interest
  and tax. EBIT margin, P/EBIT and EBIT CAGR consequently exposed PBT under an
  EBIT label.
- CFO minus capex was presented as bank free cash flow even though operating
  cash flow for a deposit-funded balance sheet is dominated by movements in
  loans and deposits. It is not comparable with corporate FCF.
- the three bank ratios used closing or partial CVM accounts: interest result
  over closing total assets, administrative expenses over spread plus fees, and
  provision over the closing net loan book.

The public methodologies use paired inputs that the CVM files do not provide.
Banco do Brasil defines global spread as gross financial margin over average
earning assets and defines the average as the arithmetic mean of month-end
balances. Its efficiency denominator includes every operating component named
in the disclosure. Bradesco likewise publishes an explicit IEO perimeter and
an annualized credit-cost basis. Banco Central credit-margin methodology uses
twelve months of flows over a thirteen-month average stock and adjusts credit
loss expense for provision movements and net write-offs.

The important invariant is therefore not one universal subtotal code. It is
that numerator, denominator, averaging convention, period and consolidation
scope come from one explicit public methodology.

## Decision

- A bank's `ebit` is null. Profit before tax is never stored under that name.
  `ebit_margin`, `ebit_cagr_5y`, `price_to_ebit`, `net_debt_to_ebit` and
  `ev_ebit` are inapplicable under the bank regime.
- Generic `fcf`, `price_to_fcf` and `fcf_yield` are inapplicable under the bank
  regime. The CVM cash-flow lines remain mirrored facts; they do not become a
  bank valuation measure.
- The bank-only ratios consume six explicit, paired inputs:
  - `net_interest_margin` = annualized documented bank interest result / average
    earning assets;
  - `efficiency_ratio` = complete operating/administrative expenses / complete
    operating income under the disclosure's own definition;
  - `cost_of_risk` = annualized documented credit-loss expense / average credit
    portfolio on the same gross, expanded or prudential perimeter.
- Flow numerators arrive already annualized on the public source's stated day-
  count basis. Expenses are normalized as positive magnitudes. A closing balance
  never substitutes for an average, and a partial CVM subtotal never substitutes
  for a complete disclosed perimeter.
- All inputs for one ratio must come from the same consolidated, managerial or
  prudential disclosure and period. No parent flow is paired with a consolidated
  stock unless the public field is explicitly named as mixed-scope.
- The current provider remains the CVM structured mirror. It cannot fill these
  six inputs, so all three ratios are null with
  `MISSING_REGULATORY_DISCLOSURE`. A future provider may use public Banco Central
  or issuer disclosures, but must preserve the paired scope instead of deriving
  an approximation from unrelated accounts.
- Existing persisted bank values for the superseded fields are invalidated by
  migration 0018. Running `smaug analyze` after the formula corrections rebuilds
  all views; until a regulatory provider exists, the three bank ratios remain
  named nulls.

## Consequences

- A number displayed as EBIT is EBIT; bank PBT no longer leaks into operating
  margins, growth or valuation multiples.
- Bank FCF no longer turns deposit and loan-book movements into a corporate cash
  conversion signal.
- BBAS3 and BBDC4 contract fixtures reconcile the three formulas to their public
  disclosures, including each source's averaging and complete efficiency scope.
- The UI explains that the current null is a missing regulatory disclosure, not
  a missing CVM line and not a zero.
- Coverage falls temporarily for the three bank ratios. This is deliberate:
  faithful nulls are preferable to complete but differently based numbers.

## Primary sources

- [Banco Central — methodology for credit margin by portfolio subgroup](https://www.bcb.gov.br/conteudo/relatorioinflacao/EstudosEspeciais/EE052_Metodologia_de_apuracao_da_margem_de_credito_por_subgrupos_da_carteira.pdf)
- [Banco do Brasil — 4T24 performance analysis](https://api.mziq.com/mzfilemanager/v2/d/5760dff3-15e1-4962-9e81-322a0b3d0bbd/4fefec94-09ca-ec01-47d5-7a57b8eab725?origin=2)
- [Bradesco — financial and economic analysis carrying the 4T24 bases](https://api.mziq.com/mzfilemanager/v2/d/80f2e993-0a30-421a-9470-a4d5c8ad5e9f/2fc6dc7c-754f-166c-ba13-eb9dd0360413?origin=1)
