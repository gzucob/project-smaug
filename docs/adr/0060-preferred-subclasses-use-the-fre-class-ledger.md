# 0060 — Preferred subclasses use the FRE class ledger

- **Status:** Accepted
- **Date:** 2026-08-13

## Context

ADR 0014 requires company market capitalization to sum every listed share class
at its own price. The original implementation could do that for one ordinary and
one aggregate preferred class because the FRE `capital_social` member files only
three quantities: ordinary, preferred and total shares.

B3 also lists class-A and class-B preferred shares under codes ending in 5 and 6.
Treating both as the aggregate preferred class would multiply the same count more
than once. Refusing every company with two preferred codes avoided that error but
left a named-null gap at exchange scale.

The same official FRE archive contains a child member named
`capital_social_classe_acao`. Its rows join the aggregate capital row on company,
reference date, version and `ID_Capital_Social`, and file both the preferred class
label and that class's quantity. For example, Banrisul's 2025 form files its PNA
and PNB counts separately and their sum reconciles to the aggregate preferred
count.

The DFP capital composition still files treasury shares only as ordinary and
aggregate preferred balances. A non-zero preferred treasury balance therefore
cannot be allocated between PNA and PNB without another source fact.

## Decision

- `CAPITAL` parser version 2 mirrors both FRE members. Each aggregate paid-in row
  carries the child class rows that match its complete filed parent key; ingestion
  preserves the CVM labels and performs no financial calculation.
- FCA codes ending in 5 and 6 identify separate PNA and PNB listed classes. The
  company registry admits at most one live symbol per ON, PN, PNA and PNB class,
  rather than at most one symbol across every preferred class.
- Analysis reads PNA and PNB counts from the joined FRE child rows. A generic PN
  count is the aggregate preferred count less every explicitly named preferred
  subclass; it is never the aggregate reused beside PNA or PNB.
- Market capitalization remains the ADR 0014 identity:

  ```text
  market cap = sum(class price * outstanding shares of that class)
  ```

- A zero aggregate preferred treasury balance leaves the filed subclass counts
  unchanged. A non-zero balance is subtracted from a subclass only when exactly
  one preferred economic class exists. With multiple preferred classes, their
  outstanding counts become unavailable and the whole cap is
  `missing_share_count`; the implementation does not guess an allocation.

## Consequences

- Companies with PNA and PNB and no ambiguous treasury allocation are capitalized
  with one independently priced term per listed class.
- A missing class row, duplicate class label, irreconcilable class total or
  ambiguous preferred treasury balance produces a named null instead of a partial
  or double-counted company value.
- Existing mirrored `CAPITAL` documents lack the child rows. Historical analysis
  must reingest `CAPITAL` with parser version 2 and then recompute persisted views
  before the new coverage appears.
- No PostgreSQL migration is required because the persisted market-cap contract
  and field are unchanged; only its primary-source inputs become more precise.

## Primary sources

- [CVM — Formulário de Referência (FRE)](https://dados.cvm.gov.br/dataset/cia_aberta-doc-fre)
- [CVM — FRE data dictionary](https://dados.cvm.gov.br/dados/CIA_ABERTA/DOC/FRE/META/meta_fre_cia_aberta.zip)
