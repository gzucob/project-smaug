# 0047 — The feed outranks the grid when it explains the tape better

- **Status:** Accepted
- **Date:** 2026-08-04

## Context

#202 (found while re-measuring ADR 0037's floor for #188): `_gap_window`
stays open through the year after the later filing (ADR 0038, to cover filing
lag), generous enough that a gap still waiting on its *next* FRE also reaches
into a real, later, independently-confirmed action that belongs to that
future gap instead. `_witnessed_ratio`'s grid then rounds the *older* gap's
dirty filed number to the nearest plausible fraction — the date lines up
because the feed event really is in the window, the size does not:

```
SOJA3   FY2023->FY2024 filed=1.15521   grid candidate=1.15385 (15/13)
        B3 feed @2025-12-15            factor=1.04863
        tape @2025-12-19                ratio=1.04434
```

The feed and the tape agree with each other (1.049, 1.044 — 0.4% apart) on an
event the grid's guess is 10% from either. `_reconciled_ratio` (ADR 0038)
already refuses to let a feed factor stand in for the counts unless it
reconciles with the *filed* move — which is exactly why these gaps fall
through to `_witnessed_ratio` at all: the feed's product does not reconcile
with the dirty filed number (that is what "dirty" means), so the stronger
witness is never consulted past that point today.

## Decision

**Once the grid's candidate has passed the existing two-witness gate, a feed
event in the same window gets one more say — not a veto, a comparison.** If
its own factor is a *closer* match to what the tape actually observed than the
candidate is, the feed's factor is used instead:

```python
if feed_found and (feed_product > 1) == (observed > 1) and \
   abs(feed_product - observed) < abs(candidate - observed):
    return feed_product
return candidate
```

A veto (discard the match outright whenever the feed disagrees) was measured
first and rejected: TRIS3's candidate (1.923) is a real match ADR 0037 already
validated at 3% residual error, and a plain veto would have thrown it away.
What it needed was correcting, not discarding — TRIS3's own feed factor (2.0)
turned out to be *closer still* to what the tape saw (6.1% vs 9.7%), which is
the comparison this decision generalizes.

The comparison is against the **tape's observed ratio**, not against the
candidate or the filed number — the tape is the one witness available to both
sides of the question, so it is what decides which of them explains the
session better.

## Consequences

Exchange-wide, before and after, over every gap `_witnessed_ratio` currently
resolves (506 codes, floor unchanged): **4 changed** — BRML3 (2017),
INEP3 (2023), LWSA3 (2020), TRIS3 (2017). All four move from the grid's guess
to the feed's factor. Measured against a live, independent witness (Yahoo's
plain `close`, which Yahoo itself back-adjusts for splits — the same basis
ADR 0045 used):

```
better  11   worse  0   unchanged  0   no yahoo  5 (BRML3, delisted/renamed
                                                     off Yahoo's coverage)
```

INEP3 goes from ~20% error to under 0.5% in every measured year; LWSA3 from
14% to 0.2%; TRIS3 — the case that ruled out a veto — from ~4% to under 1%.

**This does not reach the five gaps #188 found blocked by ADR 0037's floor.**
Three of them (CGRA3, CGRA4, SOJA3) would be corrected the same way if the
floor ever lifted — checked directly, not assumed. The other two (DEXP3,
DEXP4) would not: their feed factor (1.125) explains that session's tape
(1.188) *worse* than the grid's guess (1.16667) does, so this decision leaves
them exactly as before. All five stay behind the floor regardless, so nothing
here changes what is persisted for them today — #188's finding stands
unmodified.

**What this does not do**: reconcile the feed with the filed count the way
`_reconciled_ratio` does, or backfill the *later* gap the feed event actually
belongs to — that gap is not yet observable (its FRE has not been filed), and
inventing it is not this decision's job. The feed's factor is trusted here
strictly as a better answer to "what ratio explains this tape reading",
nothing more.
