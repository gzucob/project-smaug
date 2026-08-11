# 0055 — A security price must meet that security's own rights

- **Status:** Accepted
- **Date:** 2026-08-11

## Context

The original `pe`, `pb` and `dividend_yield` were whole-company ratios: market
capitalization divided by company profit, equity or cash distributions. Those
figures can be useful, but the unqualified names implied a security-level result
and made sibling classes display the same value even when their prices, CPC 41
results and cash rights differed.

CPC 41 requires basic and diluted earnings per share for the relevant class.
B3 defines P/E as security price divided by earnings per share and P/B as
security price divided by book value per share. B3's dividend-yield definition
likewise compares distributions per share with that security's price.

The B3 cash-event mirror already preserves the facts required for the numerator:
share class, absolute value, explicit quotation lot, last cum-right date and
approval date. FCA separately declares which class quantities compose a unit.
Historical B3 prices are restated for later splits, groupings and bonuses, so an
older cash amount must be moved through the same later share-base changes before
the two sides can be divided.

DFC cash outflows, DMPL declarations and B3 ex dates answer different timing
questions. In particular, an ordinary meeting after year-end can declare a
distribution from the prior exercise. The structured inputs do not prove the
source exercise of every distribution, so an unqualified `payout` would claim
more than they establish.

## Decision

This decision supersedes ADR 0039 only where that ADR left units without a
composed cash-right numerator. Its percentage-based total-return adjustment is
unchanged; this decision adds the distinct absolute-cash basis required by
dividend yield.

- `pe_basic` is the analyzed security's B3 price divided by its filed CPC 41
  basic result. `pe_diluted` uses the corresponding diluted result. Neither
  substitutes closing shares for CPC 41's weighted denominator.
- `pb` is the analyzed security's price divided by closing BVPS. Controller
  equity is allocated over all outstanding underlying shares; a unit carries
  the sum of its FCA-declared component quantities. This is a closing allocation,
  not a claim that preferred classes have identical contractual liquidation
  preferences.
- `company_pe` and `company_pb` retain the former whole-company formulas under
  explicit scope names: company capitalization divided by controller profit or
  equity.
- `distributions_per_security` sums B3 absolute cash rights whose ex date lies in
  the view's window. B3's `quotedPerShares` scale is applied exactly. Each older
  amount is divided by every later share-base action, using its last cum-right
  session, so it matches the restated historical price basis.
- A unit distribution is the FCA composition sum: component quantity multiplied
  by that component class's B3 cash per share. Missing composition or an
  unreadable relevant B3 amount makes the result null with a named cause.
- Closed-year `dividend_yield` uses calendar-year ex dates and that security's
  restated nominal B3 average for the year. Live TTM uses ex dates from the last
  twelve months and the current security price.
- DFC- and DMPL-derived fields retain their company scope and state their timing:
  `payout_cash_paid_in_period`, `payout_declared_in_period`,
  `company_cash_yield_paid_in_period` and
  `company_yield_declared_in_period`. No field attributes a distribution to a
  profit exercise without source evidence that establishes that link.
- `CAPITAL_EVENT_B3` and `CASH_DIVIDEND_B3` are persisted and read with
  `source="b3"`. The registrant key still comes from CVM's public company
  identity, but the source component of the raw identity names the authority
  that published the payload. Every source adapter declares that authority in
  its `RawFetchResult`; the writer persists the declared value instead of
  inferring it from the registrant. The module registry pairs the same source
  with its parser identity for planning, resume checks and failure records.
- An accepted `CASH_DIVIDEND_B3` validation with established coverage and zero
  rows proves an empty cash-rights set. Missing coverage, a quarantined latest
  validation or an expected amount that cannot be read produces a named null;
  it must not silently become zero dividend yield.

## Consequences

- Sibling ON/PN/PNA/PNB securities can have different P/E, P/B and dividend
  yield while retaining the same explicitly company-level ratios.
- Units receive a holder-level dividend yield from their declared bundle instead
  of inheriting one component or going silently company-wide.
- A same-day split and cash ex event remains on one base because the amount is
  anchored on the last cum-right session, as the historical close is.
- Closed-year and live yields state different windows and price instants; they
  are intentionally not interchangeable.
- Historical rows must be recomputed after migration. Renamed company fields can
  preserve their stored values, while the new security fields remain null until
  analysis reads the B3/CVM inputs again.
- Existing local B3 modules stored under the former `source="cvm"` identity are
  rebuilt from the public endpoints instead of being copied or coexisting with
  corrected `source="b3"` documents.

## Primary sources

- [CPC 41 — Resultado por Ação](https://www.cpc.org.br/Arquivos/Documentos/430_CPC_41_rev%2003.pdf)
- [B3 — como calcular P/L e P/VP](https://borainvestir.b3.com.br/objetivos-financeiros/investir-melhor/como-calcular-a-rentabilidade-de-acoes-entenda-com-o-bora/)
- [B3 — definição e cálculo do dividend yield](https://borainvestir.b3.com.br/objetivos-financeiros/investir-melhor/dividend-yield-o-que-e-e-como-calcular/)
- [CVM — Lei 6.404, assembleia ordinária e destinação do resultado](https://sistemas.cvm.gov.br/port/atos/leis/6404.asp)
