# 0029 — Passivo/Ativo and PL/Ativo use different equity slices, and do not sum to 1

- **Status:** Accepted
- **Date:** 2026-07-30

## Context

Two balance-sheet structure ratios are published side by side:

- `liabilities_to_assets` — "Passivo/Ativo", the share of the assets financed by
  third-party capital.
- `equity_to_assets` — "PL/Ativo", the share financed by the shareholders.

Both were computed against the **controllers'** equity, which made them exact
complements and made the pair easy to explain: one was `1 −` the other. It also
meant `liabilities_to_assets` counted the minority interest as third-party
capital, which under CPC 26 / IFRS 10 it is not — a non-controlling interest is
presented *inside* equity.

Publishing the absolute balance-sheet figures (#142/#144) made the inconsistency
visible on one screen: `total_liabilities` is `assets − equity_total`, so the
published "Passivo total" was not the numerator of the published "Passivo/Ativo".
The two disagreed by exactly the minority interest.

Neither the fidelity fixture (#44) nor any test pinned these two ratios, so the
reference platforms were consulted directly. Investidor10 publishes both, and
**its own two figures do not sum to 1**:

| | Passivos/Ativos | Patrimônio/Ativos | sum |
|---|---|---|---|
| WEGE3 | 0,57 | 0,40 | 0,97 |
| ITSA4 | 0,17 | 0,79 | 0,96 |

Which slice each uses is decidable from the platform's own published equity and
assets, without our data: `1 − PL/assets` would give 0,60 for WEGE3 and 0,21 for
ITSA4. It publishes 0,57 and 0,17 — lower, by a plausible minority interest in
both cases. So its "Passivos" excludes the minority while its "Patrimônio" is the
controllers' slice.

## Decision

The two ratios sit on different slices, and the residual between them is named
rather than hidden:

- `liabilities_to_assets` = `(total_assets − equity_total) / total_assets` —
  third-party capital only. It is `total_liabilities / total_assets` by
  construction.
- `equity_to_assets` = `equity / total_assets` — the controllers' slice,
  unchanged. It is what pairs with `bvps`, `pb` and the per-share family.
- **They are not complements.** `liabilities_to_assets + equity_to_assets +
  minority/assets = 1`, and any copy describing one as the arithmetic complement
  of the other is wrong and is corrected.

## Consequences

The published "Passivo/Ativo" now equals the published "Passivo total" over the
published "Ativo total" — a reader can check our arithmetic on the same screen,
which they could not before. The label "fatia dos ativos financiada por
terceiros" becomes true rather than approximately true, and both ratios agree
with the reference platform's basis.

The cost is that the pair no longer explains itself as `1 − x`. A reader who adds
them and gets 0,97 must be told why, so the two indicator docs state the residual
explicitly instead of claiming complementarity. That is a real loss of
simplicity, accepted because the simpler version was simple *and wrong*: it
answered "how much of the assets is not the controllers'?" while the label
promised "how much is owed to third parties?".

It moves a published number on the tickers that consolidate subsidiaries they do
not wholly own — WEGE3 by 2,66 pp, VALE3 by 0,97, BBAS3 by 0,18, PETR4 by 0,15,
BBDC4 by 0,02 — and by nothing at all on the five that carry no minority
interest. It does not touch `equity_to_assets`, `bvps`, `pb` or anything else on
the controllers' basis.

This does not adopt the dual-basis `_total` sibling pattern of ADR 0026 for these
two. That pattern exists for indicators where both slices answer a question a
reader actually asks; here the controllers' reading of *liabilities* answers
none — it is an accounting category error, not a second point of view.
