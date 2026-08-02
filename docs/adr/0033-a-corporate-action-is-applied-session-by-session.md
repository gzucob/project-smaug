# 0033 — A corporate action is applied session by session, not to the year's average

- **Status:** Accepted
- **Date:** 2026-08-02

## Context

ADR 0027 restates share counts onto the current base, and ADR 0032 replaced the
vendor price with B3's own series, which publishes the price **as traded**. So
the price has to be divided by the same restatement the counts were multiplied
by, or `cap = price × shares` moves. That is what `RestatedPriceProvider` does.

It divided the *year's average* by the *year's factor*, because the counts are a
yearly series and the factor was defined per filing year. Measured against the
vendor's back-adjusted series — an independent witness, since it embodies the
true factor — the result was right for most companies and wrong for the ones
with a history of actions, in a way that got worse the more of them there were:

```
                 as traded   restated   vendor   error
BBDC4 2018           33.39      25.26    20.38   23.9%
BBDC4 2022           19.07      19.07    18.51    3.0%
WEGE3 2020           55.21      55.21    27.56  100.4%
SAPR4 2019           15.41      15.41     5.13  200.0%
```

The cause is not a missing event. It is that a year *containing* an action has no
single base: the sessions before it are quoted on the old one and the sessions
after it on the new. A yearly factor has to choose, and it chose "the whole year
is pre-action" — so WEGE3's 2020, split in the middle, was left unadjusted
entirely by a factor that belonged to 2019, while its post-split second half had
already halved on the tape.

The dates exist. CVM files the approval date of every declared action (#174), and
the filings the inference reads are yearly by nature.

## Decision

The restatement is published to the price side as a **dated timeline** —
`restatement_timeline`, a sequence of `(effective, ratio)` — and a price is
divided by the product of the ratios that take effect **after the session it
printed on**.

- A **declared** action is dated by its own approval.
- An **inferred** move has no date to its name — all two filed counts say is
  that the base had moved by the next filing — so it takes the first day of the
  reporting filing year, which is exactly where the per-year factor already put
  it. Inference behaves as it always did; only a declaration buys the finer split.
- A year's average takes **one** divisor still, but a session-weighted one:
  `Σp / Σ(p/g)`, so that dividing by it yields the mean of the restated closes.
- The **counts keep the yearly factor**. A count series has one value a year;
  there is no session to hang a finer one on.

B3's yearly archive is therefore reduced keeping the **daily closes**, not only
their mean. It costs ~3 MB a year on disk (a year holds ~336k spot-market
records, not the millions the 748 MB archive suggests), and they are held encoded
as one short string per code so a decade of years in memory stays in megabytes.

## Consequences

Measured over 8 tickers × 10 years against the vendor series: **16 cells closer,
0 further**. BBDC4's residual falls from 13–24% to ~1.5%, WEGE3 2020 from 100.4%
to 0.18%, SAPR4 2019 from 200% to 0%.

**The price's factor and the count's factor are no longer the same number**
inside a year that holds an action — BBDC4 2017 divides the price by 1.775 and
multiplies the count by 1.586. That reads like a violation of the rule that gave
this decorator its shape, and is not one: it is the rule applied to a quantity
that varies within the year. `mean(pₛ/g(s)) × N_today = mean(pₛ × nₛ)` whenever
`nₛ × g(s) = N_today`, which is what a complete chain guarantees — so the cap
becomes the true **average cap over the year**, where before it was a session
average price times a point-in-time count. Cap invariance in the strict sense
still holds for every year that holds no action, which is every year but a
handful.

What this does **not** fix is coverage: BBAS3 2023/2024 and VIVT3 2024 remain
100% out, because CVM stopped filing the declaration member after the 2023 FRE
and B3's own event feed is not ingested yet. Those cells move when it is, and
they move without touching anything decided here — a factor that arrives with a
date is what this ADR already consumes.

The residual left where coverage *is* complete is ~1.5%, and its cause is now
identified rather than unexplained: CVM files the **approval** date, the market
reprices on the **ex** date, and the two are days apart. Closing it needs the ex
date, which B3's feed carries and CVM's filing does not.
