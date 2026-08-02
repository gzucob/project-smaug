# 0035 — The price series dates its own corporate actions

- **Status:** Accepted
- **Date:** 2026-08-02

## Context

ADR 0033 cut the price series on the day a corporate action took effect. ADR 0034
got that day from B3's `GetListedSupplementCompany` feed, matched to a step by an
exact ratio and adopted only where the pairing was unambiguous in both
directions. It closed the four large errors and left a class of them open,
because **the feed is not a record of what happened**: it lists one Bradesco
bonus (2022) where CVM's file lists nine, one for Itaúsa, and nothing at all for
half the actions the market actually priced.

The record does exist, and we were already downloading it. COTAHIST carries two
fields the reader ignored:

| Field | Position | What B3's layout says it is |
|---|---|---|
| `DISMES` | 243–245 | "número de seqüência do papel correspondente ao **estado de direito vigente**" |
| `ESPECI` | 40–49 | the specification, whose second token carries the "ex" markers |

`DISMES` is B3's own statement that the rights attached to the share changed, and
it steps on the first session quoted on the new base. BBAS3, 15 and 16 April
2024:

```
20240415  ON      NM   56.46   dist=321
20240416  ON  EB  NM   27.91   dist=322
```

Measured over the 60 most traded Brazilian companies, 2015–2026: the feed lists
**47** actions, the tape marks **90**, and **all 47 of the feed's are among
them** — the extra 43 include all eight of Bradesco's bonuses and all eight of
Itaúsa's. Where both sources name an action, `lastDatePrior + 1` and the marked
session are the same day.

What the tape does not carry is a **ratio**. The price moved across that session
by the action *and* by the day's own trading. Over 107 pairs where both sources
name the action, the observed ratio sits 1.5% from the declared one at the
median, 3.5% at the ninth decile and 10.3% at its worst.

## Decision

**The restatement chain is dated off the price series itself**, behind B3's event
feed and ahead of everything else.

- The unit of reading is the **span** of one distribution number, not a session.
  A span is a base change when its markers carry a `B` (bonificação — which is
  how the tape marks a desdobramento too) or a `G` (grupamento) that was **not
  standing on the session before it opened**, and it is dated by the session it
  opened on.
- Both halves of that rule are load-bearing, and each was measured wrong first:
  - *Read the span, not its first session.* Itaúsa's December 2025 bonus steps
    the distribution on the 19th under `EX` and only says `EB` from the 22nd,
    still on the same number. So does VIVT3's 2025 split-and-grupamento. `EX` is
    what B3 writes where a session carries more than one event; it needs no rule
    of its own once the span is what is read.
  - *Compare against the previous session, not the previous span.* The marker is
    sticky for about eight sessions, so Itaúsa's interest goes ex as `EJB` ten
    days after its bonus went ex as `EB`, and the `B` on the second belongs to
    the first. But comparing whole spans hides a bonus that follows a bonus —
    SLC Agrícola's May and December 2023 actions are consecutive spans.
- `ED`, `EJ` and `ES` never open one. Cash leaving the company drops the price
  without creating a share (253 of them moved a price more than 15%), and a
  subscription issues shares against new money — a dilution, which ADR 0027
  already restates by nothing.
- **A session never contributes a ratio**, for the same reason an exchange action
  does not (ADR 0034): it has no share count to anchor one on. Its observed ratio
  is used only to tell *which* declared action a session belongs to.
- A step takes a session when the session falls inside the step's **window**, the
  observed ratio is within **±25%** of the declared one, and the pairing is
  unambiguous in both directions. The window is what a step can honestly claim to
  know: from its approval to six months later when CVM declared it, and from the
  first day of the filing year to the end of the following one when it did not —
  because the FRE reports an action a year late.
- The years offered run one past the last filed year, for that same reason.

The ±25% band is twice the worst deviation measured and an order of magnitude
short of the confusion it exists to reject — Magalu's 1:10 grupamento against its
5% bonus, both inside one window.

## Consequences

**Bradesco, Itaúsa and every company that pays a routine bonus are dated for the
first time.** They were the largest remaining class: a declared step sat on CVM's
approval, weeks before the market repriced, and ADR 0034's exact-ratio match
could not reach them because the counts read 1.0966 where B3 states 1.1.

**The feed stays wired and first in precedence**, because it states an exact
factor where the tape offers only the market's reading of one. It is no longer
load-bearing for coverage: on this sample the tape names everything it names and
43 more.

**A rule is written on undocumented values.** B3 publishes no table for the "ex"
markers; the layout's ESPECI table lists classes only. The reading is measured
rather than cited, so it lives behind one function with its evidence, and it is
covered by tests that fail when the stickiness rule is dropped. A format change
here is silent — the risk ADR 0032 accepted for the price is now also taken for
the dates.

**The reduction keeps a rights series and is rebuilt.** One entry per code-year
where either the distribution number or the marker moved, which is a few per year
against ~250 sessions. `_REDUCTION_VERSION` goes to 4; the archives are streamed
once more.

**An illiquid code is dated less well.** The marker runs for about eight
sessions, so a code that trades less often than that can change its rights state
with no marked session to name it. The distribution number still moves, which
brackets the event between two sessions rather than placing it — such a code
keeps its filing-year date rather than getting a wrong one.

**A code's earliest requested year cannot open with an action.** The state a
first session moved from lives in the previous file, so the reader walks the
years in order; the first has nothing before it. SLCE3's 3 January 2022 bonus is
invisible reading 2022 alone and appears with 2021 beside it.

**What is still missing is a ratio, not a date** — unchanged from ADR 0034.
TOTS3's 3:1 split is dated here and still restated by nothing, because no filed
count matches the base CVM's declaration names (#176).
