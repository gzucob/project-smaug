# 0040 — The exchange is the default price source

- **Status:** Accepted (completes the default [0032](0032-b3-publishes-the-price-series-itself.md) deferred)
- **Date:** 2026-08-02

## Context

ADR 0032 chose to read B3's own published series (`COTAHIST_A{year}.ZIP`) but
left `PRICE_SOURCE` defaulting to the vendor chain, for one reason: the exchange
publishes the price **as traded**, while the share counts are restated onto the
current base (ADR 0027). Two numbers on two bases multiply into a wrong cap, so
selecting the exchange would have mispriced every company that ever split.

That reason is spent. The restatement landed and was built out of the exchange's
own record: applied session by session rather than per year (ADR 0033), dated by
B3's event feed (ADR 0034) and by the tape's own rights state where the feed is
silent (ADR 0035), with the ratio always anchored on a filed share count (ADRs
0036–0038). The third basis — the dividend-adjusted one, which no source
publishes — is rebuilt from B3's payout record (ADR 0039), and the payouts are
now mirrored.

What the vendor chain is, measured rather than assumed:

- **It mixes the two bases itself.** It back-adjusts the price onto today's base
  and leaves the count as filed. AZEV3's 2015 exercise priced that way gives
  Azevedo & Travassos a market cap of R$ 4.08 billion; on the exchange's series,
  with both sides on the year's own base, it is R$ 51 million. This is ADR 0027's
  warning arriving from the other direction — we guarded our own arithmetic
  against it and consumed a vendor that does not.
- **It does not cover the exchange.** 48 cells that had no price at all under the
  vendors have one from B3 — the small caps Yahoo answers 404 for (#164).
- **It is slow, and for nothing.** The vendor is asked once per ticker **per
  year**: ~6,000 sequential HTTP calls for a whole-exchange run, about 50
  minutes. The same run reading the exchange's cached archives takes 3 minutes,
  because a year's file is read once and served to every company in it.
- **It is not a witness we can audit.** Its adjustments are undocumented and it
  is demonstrably not a witness for a recent action (#188), whereas COTAHIST is
  a published file whose every field we read is specified.

## Decision

`PRICE_SOURCE` defaults to **`b3`**. The exchange's own series prices the
analysis, wrapped in the restatement (ADR 0033) and the dividend basis (ADR
0039).

`vendors` remains selectable, and only that: it is kept until the Yahoo/brapi
chain is removed altogether (#67). It is not a fallback the code reaches for on
its own — a company the exchange does not list reads as a missing price, loudly,
rather than silently from a second source on a different basis.

## Consequences

The price and the count now sit on the same base by construction, for every
company, without depending on a third party's undocumented adjustment. Prices
become auditable: any number in the analysis can be traced to a line of a
published file. A whole-exchange run stops costing an hour of somebody else's
rate limit, which is what made re-running it feel expensive enough to skip.

It costs the history of a **renamed** ticker. B3 files each year under the code
that traded then, so AZZA3 has no 2015 (ARZZ3 does), and the same for ALOS3,
B3SA3 and AMER3 — 47 cells lost their price in the slice measured, and with it
every price-derived indicator of that exercise. This is tracked as #193 and is
a gap in *our* reading, not in the source: the registrant is the same company
throughout, and the mirror is already keyed on it (ADR 0030).

It also ends the comparison against the vendors as a matter of course. The
exchange and CVM are the sources of truth; a platform was a pointer, never an
approval criterion, and that scaffolding comes down with the chain it was built
against.
