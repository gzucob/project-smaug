# 0057 — Valuation stocks share one cutoff and one perimeter

- **Status:** Accepted
- **Date:** 2026-08-11

## Context

The closed-year market value multiplied each class's mean daily price by its
closing outstanding count. That result was neither B3's point-in-time market
value nor an average market value: an issuance or buyback changed the count only
at one instant but the formula priced that closing count as if it had existed all
year. [B3 defines company market value](https://sistemaswebb3-listados.b3.com.br/marketValuePage/stockExchangeMonth)
from the latest available quotation of each class and the corresponding share
base.

The liquidity bridge had a second basis mix. It subtracted CVM lines 1.01.01
(``Caixa e Equivalentes de Caixa``) and 1.01.02 (``Aplicações Financeiras``)
together. [CPC 03](https://www.cpc.org.br/Arquivos/Documentos/183_CPC_03_R2_rev%2024.pdf)
admits an investment as a cash equivalent only when it is
immediately convertible to a known amount and exposed to insignificant value
risk, normally with maturity of three months or less from acquisition. The
aggregate 1.01.02 line does not prove those properties.

Finally, consolidated EBIT and EBITDA were divided into numerators built only
from the controlling shareholders' interests. Enterprise value omitted
non-controlling interests, and ROIC used controllers' equity.
[CPC 36](https://www.cpc.org.br/Arquivos/Documentos/448_CPC%2036%20R3%20rev%2004.pdf)
places the non-controlling interest inside consolidated equity: it participates
in the same group result as the consolidated operating lines.

## Decision

- A closed-year valuation is a point-in-time stock at the fiscal cutoff. For
  every listed class it multiplies B3's last available close in the year by the
  CVM year-end outstanding count on the same current share base, then sums the
  classes. The analyzed security's price uses that same close. The nominal and
  dividend-adjusted averages remain historical series, not valuation inputs.
- Net debt subtracts only the filed 1.01.01 cash-and-cash-equivalents line.
  Current financial investments under 1.01.02 are published separately. They
  are not promoted to cash equivalents from an aggregate code that carries no
  maturity, convertibility or value-risk evidence.
- Non-controlling interests are ``consolidated equity − controllers' equity``.
  Enterprise value is ``market cap + net debt + non-controlling interests`` so
  EV/EBIT and EV/EBITDA have the same consolidated perimeter on both sides.
- The existing normalized-tax return is published as ``roic_statutory``:
  ``consolidated EBIT × (1 − 34%) / (consolidated equity + net debt)``. The flat
  Brazilian statutory proxy remains the deliberate comparability choice from
  ADR 0002; its name and stored ``roic_tax_basis`` no longer imply an issuer's
  effective tax rate.
- Every analysis row stores its price, share-count, liquidity and ROIC-tax
  bases, including rows whose source input is missing. Provenance describes the
  requested arithmetic, not only successful results.

## Consequences

- Closed-year market value and every multiple built on it become reproducible
  point-in-time measures. They will move from the former annual-average basis;
  this is a semantic correction, not a data refresh.
- A company with material 1.01.02 investments reports higher net debt than
  before. The broader liquidity remains visible without claiming CPC 03
  eligibility that the statements do not establish.
- EV rises by the non-controlling interest where one exists, and statutory ROIC
  uses consolidated invested capital. Companies without minority interests are
  unchanged on that dimension.
- Computing a true average market value would require dated share-count changes,
  including issuance and treasury movements. This decision does not approximate
  that unavailable series with a closing count.
- This supersedes ADR 0018's annual-average valuation basis and only the ROIC
  perimeter/naming portion of ADR 0002. ADR 0002's statutory-rate, capex and
  payout decisions otherwise stand.
