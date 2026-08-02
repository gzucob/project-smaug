# 0032 — The price comes from B3, on two bases: as traded and as adjusted

- **Status:** Accepted (supersedes [0013](0013-yahoo-primary-brapi-fallback.md) and [0011](0011-yahoo-finance-for-closed-year-price-history.md))
- **Date:** 2026-08-01

## Context

Every accounting line, share count and payout this project reads already comes
from a regulator's published archive. The **price** was the exception: ADR 0011
moved the closed-year history to Yahoo's `v8/finance/chart`, ADR 0013 made Yahoo
the primary for the live quote too, with brapi behind it. Both are undocumented
vendor back-ends — the endpoints those sites' own pages call, with no contract.
Yahoo has already closed the sibling endpoint (`v7/finance/quote`, now HTTP 401),
and the failure mode is silent: a source that stops answering degrades to a null
multiple, not to an error.

The cost stopped being hypothetical once the whole exchange was analysed (#109):
of 316,008 cells, **16,651 (5.3%) were lost to price-vendor coverage** — the
largest actionable bucket. RDNI3 traded 242 sessions in 2025 and Yahoo has no
series for it at all.

B3 publishes the whole thing itself — `COTAHIST_A{year}.ZIP`, every listed
security, one file per year since 1986, free and unauthenticated, the same shape
as the CVM archives the ingestion context already handles. Measured: 520 MB for
2015–2026; the running year's file already carries the same day's session; and
the codes the vendors 404 are all there (RDNI3 242 sessions, SRNA3 220, LIPR3 14,
EQMA3B 127 in 2025).

### The finding that shaped the decision

Swapping the source is **not** a like-for-like substitution, and assuming it was
would have shipped a systematic error. Diffing both sources over 308 cells
(28 tickers × 2015–2025):

```
308 cells | both priced 233 | agree 108 | diverge 125
  >= 5% (corporate action) 102 | 1-5% 8 | < 1% 15
```

The divergences are not noise — they are exact factors, and they compound
backwards through time:

| | B3 / Yahoo | event |
|---|---|---|
| BBAS3, all 10 years | ×2.0000 | the 2:1 bonus |
| VIVT3, all 11 years | ×2.0000 | the 2:1 split |
| HAPV3 | ×0.3333 then ×0.0667 | two groupings |
| SAPR4, TOTS3 | ×3.0000 | splits |
| BBDC4 | 2.2381 → 1.9756 → 1.8065 → 1.6382 | annual bonuses compounding |

**Yahoo back-adjusts its `close` for every corporate action; COTAHIST does not
adjust at all.** ADR 0027 already depended on this without a second source to
check it against: it restates share counts onto the current base *because* "the
price's base is, irrevocably, the current one". Pairing an as-traded price with a
restated count would have overstated BBAS3's pre-2024 cap by exactly 2×.

The reference platforms treat this as two distinct products, not one right
answer: Investidor10 offers **"Cotação padrão | Cotação ajustada"** as a toggle
on the asset's own page, and Status Invest documents its chart as adjusted for
splits, groupings and bonuses but **not** for dividends.

## Decision

**B3's published series is the price source, and both bases are kept.**

- The **as-traded** price is what B3 publishes and what we store unmodified: the
  mean of a closed year's daily closes, and the last close for the live quote.
- The **corporate-action-adjusted** price is *derived* from it, by dividing by the
  very factor ADR 0027 multiplies that year's share counts by
  (`restatement_factors`). It is not adjusted for dividends — that is a third
  basis, and ADR 0018 already rules it out of valuation.
- **Indicators are computed on the adjusted basis**, which is the one ADR 0027's
  restated share counts already sit on. Since `cap = price × shares` is invariant
  under a consistent restatement, no multiple changes value; what the basis
  decides is the per-share series and the price a screen shows.

**The factor is deliberately the counts' factor, not a better one.** B3 publishes
a corporate-event feed (`stockDividends`: `DESDOBRAMENTO`, `GRUPAMENTO`,
`BONIFICACAO`) which would in principle be more precise — event-level, dated, and
able to see the composite action ADR 0027 admits it misses. Two things rule it
out. It is **incomplete**: it lists one Bradesco bonus (2022) where the measured
price ratio proves roughly one a year across the window, and one for Itaúsa. And
even a complete feed would be the wrong input here — a price adjusted by a factor
the count was *not* adjusted by breaks the invariance above, and a cap wrong by
10% is worse than a per-share series uniformly one base behind. Improving the
chain means moving both sides together, which is an evolution of ADR 0027 rather
than a choice about the price source.

The live quote becomes an **end-of-session close** rather than an intraday one.
Deliberate: over a twelve-month accounting window the two agree at any precision
that reaches a screen, while the close makes an `analyze` run **reproducible** —
two runs on the same day now produce the same number, which is what a fidelity
gate needs to mean anything.

## Consequences

- **The migration is staged, not atomic.** The reader lands first behind
  `PRICE_SOURCE`, defaulting to the vendor chain; the default moves only once the
  adjustment exists and a cell-by-cell diff is clean. Deleting Yahoo before that
  would have removed the only witness the diff can be made against.
- **No multiple changes value**, because the cap is invariant to a consistent
  base. This is an assertion the diff must prove, not an assumption — the first
  version of this ADR claimed it on a single ticker rounded to two decimals, and
  a broader measurement contradicted it.
- **A large class of nulls becomes a value**, and the ones that remain become
  honest: a code absent from a year genuinely did not trade. TAEE4 was *listed*
  in 2015 and traded zero sessions — Yahoo's missing series was faithful, and
  #164 was written against the opposite assumption.
- **Two bases must be labelled wherever a price is shown.** Holding both and
  naming neither is how a reader ends up comparing R$45.49 with R$22.75.
- **The dividend-adjusted column loses its source.** COTAHIST publishes no such
  series; it is rebuilt from B3's cash-dividend history, which — unlike the stock
  event feed — *is* complete (900 Bradesco rows back to 1995-12-28) and is a
  separate decision.
- **ADR 0027's blind spot is inherited, not fixed.** A composite action inside one
  filing year (VIVT3 2024) stays invisible. It now misses identically on both
  sides, which is what keeps the cap right regardless; the per-share series and
  the displayed price for those years stay one base behind. Tracked as #122.
- **A one-time 520 MB cache** and a first run that streams several GB of text;
  the per-year reduction keeps later runs cheap.
- **We depend on a published file rather than an endpoint** for the price itself.
  Not a contract — B3 owes us nothing — but a format change is loud where a gated
  endpoint is silent. The corporate-event feed remains an endpoint, with the same
  standing as the taxonomy's (ADR 0031).
