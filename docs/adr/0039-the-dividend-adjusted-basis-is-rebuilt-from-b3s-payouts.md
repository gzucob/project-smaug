# 0039 — The dividend-adjusted basis is rebuilt from B3's own payout record

- **Status:** Accepted
- **Date:** 2026-08-02

## Context

Three price bases exist and must never be mixed (ADR 0032): as traded, restated
for corporate actions, and adjusted for dividends. The third one is a
total-return ruler, kept out of the valuation multiples on purpose (ADR 0018)
and published as its own column.

Only the vendors ever supplied it. B3's quote file carries the price as traded
and nothing else, so removing brapi and Yahoo — the point of this migration —
takes `price_adjusted` to null for every company. ADR 0032 foresaw that and said
the column would be rebuilt from B3's cash-dividend history, which "unlike the
stock event feed *is* complete (900 Bradesco rows back to 1995-12-28)".

Measured before building: 900 rows for Bradesco, 504 for Itaúsa, 337 for
Petrobras, one per payment **per share class**, each carrying the closing price
it went ex against and `corporateActionPrice` — B3's own reading of the payment
as a percentage of that close.

## Decision

**The dividend-adjusted basis is derived from `GetListedCashDividends`, mirrored
as filed, and the factor is the percentage B3 itself publishes.**

- A payment scales down every close that **preceded** its ex date, which is the
  same "everything that postdates it" rule the corporate-action restatement
  takes (ADR 0033) with the direction reversed — there the price divides,
  here it multiplies.
- `corporateActionPrice` is taken as filed rather than recomputed from the
  value and the price. The two agree to 5×10⁻⁷ over Bradesco's history, and
  taking B3's keeps the reading free of `quotedPerShares`, which is 1 or 1000
  and applies to the payment and the reference price alike — a quarter of
  Bradesco's rows are quoted per lot of a thousand.
- A row B3 leaves without a percentage is skipped. All of them carry a
  `valueCash` of `0,0000000001`, a nominal payment that rounds to nothing.
- The year's average takes **one** factor, session-weighted, so that it yields
  the mean of the adjusted closes rather than the adjusted mean — the mirror of
  ADR 0033's weighting.
- The order is fixed at the composition root: dividends first, the share
  restatement outermost, so both bases end on the same share base and differ
  only by the cash.

## Consequences

Against the vendor's adjusted series, applying both adjustments as the pipeline
does:

```
             traded    ours    Yahoo    gap
VALE3  2024   62.08   52.78    52.73   0.09%
WEGE3  2024   45.03   42.97    42.25   1.70%
PETR4  2024   38.20   30.47    29.28   4.06%
BBDC4  2024   13.98   11.57    11.57   0.00%
ITSA4  2024   10.23    8.326    7.734  7.65%
```

Itaúsa's is the outlier and stays a question, not a verdict (ADR 0022): a
holding paying monthly, half of it as interest on own capital, where a vendor
netting withholding tax and B3 publishing the gross would diverge exactly this
way. It is not investigated here and it is not a reason to hold the column back.

**Two endpoints are needed and neither alone will do.** The supplement turns a
trading root into a `tradingName`; only the paginated endpoint carries history.
The supplement has a `cashDividends` list of its own and it is a *recent window*
— 32 rows against 900.

**A unit gets no dividend basis at all.** B3 files a rate per class and none for
the bundle, whose composition this project does not model (#38). The column is
left null rather than filled with the traded price, which for a payer would
claim the two rulers coincide.

**A company whose two B3 names disagree is mirrored as never having paid.** The
dividend table is keyed on a name no other endpoint publishes — Ambev is
`AMBEV S/A` everywhere and `AMBEV` there, and `AMBEV S` answers with nothing, so
it is not a prefix match. Shortening a name until one answers would attach
Bradesco's history to Bradesco Financiamentos, and a wrong dividend series is
worse than none. Left unguessed and filed as #190, with the blast radius
unmeasured.

**This unblocks removing brapi and Yahoo**, which is what the column was waiting
on — not that the column is finished, but that it no longer has a vendor as its
only possible source.
