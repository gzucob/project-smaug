# 0045 — The dirty-ratio floor holds, re-measured against a live witness

- **Status:** Accepted
- **Date:** 2026-08-04

## Context

ADR 0037's size floor declines `_witnessed_ratio` wherever the move is under 25%
in either direction, and its sole evidence was CYRE3 going "from an exact match
to 16% out in all eleven years" once the floor was removed. ADR 0038 then showed
that measurement unsound: Yahoo had not applied CYRE3's December 2025 bonus at
all, so the "regression" was the chain being right against a stale witness, not
the floor earning its keep. #188 asks the question ADR 0038 left open: re-measure
against a witness that is current, and decide whether the floor still earns its
place.

## Decision

**The floor stays**, and the two failure modes it is now shown to guard against
are more specific than "a witness cannot separate a 4% bonus from a 4%
follow-on."

The exchange-wide scan (`_witnessed_ratio` replayed with the floor line removed,
over all 506 traded codes) surfaces exactly 13 gaps across 8 tickers that the
floor is the *sole* reason stay unrestated — every other guard already passed.
Measured cell-by-cell against Yahoo's `close` (which Yahoo itself back-adjusts
for splits, matching our split-only basis — not `adjclose`, which additionally
strips dividends and is the wrong basis to diff against, ADR 0018) over the
years each gap affects:

```
better  21   worse  24   unchanged  3
```

(LREN3's 2013-2015 rows are excluded from this count: both readings are off by
300-500% there, a residual from a pre-2013 action neither reading captures — a
separate, unmeasured gap, not signal about this decision.)

That is not the 135-0 landslide ADR 0037 originally claimed, but re-measured
honestly it is not a landslide the other way either — and the "worse" half is
not noise. Two distinct causes explain it:

**Five tickers (ALUP11, ITSA3, ITSA4, VITT3, LREN3's 2015→2016 gap) are real
actions the floor wrongly holds back** — the filed ratio sits within 0.5% of a
plausible fraction, the tape confirms one base change of the right size and
sign, and nothing else in the gap contradicts it. Restating them roughly halves
the error against Yahoo (e.g. ITSA3 2017: 17.83% → 4.74%).

**Three tickers (CGRA3/4, DEXP3/4, SOJA3) are a gap misattributing a real,
independently-confirmed action to the wrong window** — not the coincidence ADR
0037 already knew about, but a sharper failure mode. Each of these gaps'
`_gap_window` (open through the year after the later filing, ADR 0038) reaches
into December 2025, where B3's own event feed *and* the tape agree a real action
happened — but at a ratio nothing like the "candidate" the grid matched to the
gap's dirty filed number:

```
CGRA3   filed 1.02149  candidate(grid) 1.04167   feed@2025-12-17  1.16379
SOJA3   filed 1.15521  candidate(grid) 1.15385   feed@2025-12-15  1.04863  tape 1.04434
```

SOJA3 is the clean tell: the feed and the tape agree with *each other* (1.049,
1.044) and disagree with the grid's pick (1.154) by an order the 2% witness band
should have caught but didn't, because the band compares the grid pick to the
*filed* number, never to the feed's own ratio when one exists in the window. The
grid found a small ratio that happens to sit near the filed number and near a
real event's *date* — and mistook proximity for identity. This is filed as
#202 — it changes the matching logic, not the floor, and needs its own
exchange-wide before/after.

**The remaining two (EZTC3, RENT3) are the coincidence ADR 0037 already
described**: the unrestated price already matches Yahoo to under 1%, so no
action happened there at all — the floor is doing exactly its stated job.

## Consequences

Nothing in `capital.py` changes. `_SESSION_MATCH_BAND` keeps doing double duty
as the floor and the tape-match tolerance (ADR 0037's own reasoning for reusing
it — the floor is set to the tape's own measurement error — is untouched by this
re-measurement).

**The floor's cost is now named rather than assumed**: five specific tickers
(ALUP11, ITSA3, ITSA4, VITT3, and one of LREN3's two gaps) stay unrestated below
25% and will until a sharper per-gap discriminator is found — which the
misattribution bug above is a candidate for, once fixed, since two of the three
"worse" tickers it explains are gaps a fix there would also clear without
touching the floor at all.

**A quorum of one is still not enough.** Where the exchange's own feed disagrees
with the grid's guess, that disagreement is the more reliable signal — closer to
ADR 0038's reconciled-ratio check than to ADR 0037's tape-only witness — and it
surfaced without needing Yahoo at all. The live Yahoo comparison confirmed the
floor's remaining five true positives and its two coincidences; the sharpest
finding here came from the mirror's own two B3 sources disagreeing with each
other, which is the same standard of evidence the rest of `capital.py` already
holds itself to.
