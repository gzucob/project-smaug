# 0037 — A ratio the counts leave dirty needs two witnesses

- **Status:** Accepted
- **Date:** 2026-08-02

## Context

ADR 0027 restates a filing gap only where the counts move by a *clean* small
rational, and restates a dirty move by nothing. That is what keeps a follow-on
from being rewritten as a corporate action, and it is right far more often than
not: measured over the exchange, 1,137 gaps move the counts without the chain
restating them, and **943 of those have no corporate action in them at all** —
they are issuances and buybacks, and leaving them alone is the correct answer.

The cost is the gap that holds an action *and* an issuance. Their product is
dirty, so the whole thing is declined and a real action is lost with the
dilution. ADR 0036 reached the ones CVM declared; CASH3's is not one:

```
FRE 2022   865,180,443
FRE 2023    86,957,953      ratio 0.10051 — a 1:10 grupamento plus 0.5% issued
```

The event is real and B3's tape dates it (2023-06-01, ADR 0035). Nothing anchors
its ratio.

## Decision

**A dirty ratio may be read as an action only when two independent witnesses
agree, and only when the action is large enough for either to see it.**

- The **counts** must sit within 2% of a ratio an action could plausibly have —
  both sides small, or one side a power of ten (a 2:1 split, a 10% bonus as
  11/10, a 1:15 grupamento, PDGR3's 1:100).
- The **tape** must mark exactly one base change inside the gap, of that size
  (±25%, the band ADR 0035 already measured) and in that direction.
- The move must be **at least 25% away from 1 in either direction**.

The plausible ratios are an enumerated grid rather than a derived test, because
density is the whole point: with denominators to 20 and a 2% margin, *every*
ratio is clean — 0.10051 sits 0.01% from 20/199, which is not an event anyone
declared.

Precedence is unchanged: a declared action, then a clean filed ratio, then the
composition (ADR 0028), and only then this. Where CVM declares the move its
exact ratio wins and the market's reading is not consulted.

## Consequences

Measured over the whole exchange, before and after, on the 5,544 stored views:

```
identical                     266,607
<= 0.05% (source rounding)        499
> 1%                              878      20 codes
value -> null                       0
```

Against the vendor's back-adjusted series: **135 better, 0 worse, 3 unchanged.**
RNEW3/4 and WEST3 fall from 50% and 90% error to 0.00%, TRIS3 from 158% to 3%,
VIVR3 from 90% to 6%.

**The size floor is not a safety margin, it is the finding.** Without it the
same rule measured 201 better and **34 worse**, and every one of the 34 was a
small ratio: Cyrela went from an exact match to 16% out in all eleven years on a
19/16 the counts never made, and Localiza from 0.4% to 3.5%. A 4% bonus and a
4% follow-on are arithmetically the same move, and a witness accurate to ±25%
cannot separate them. The rule can only be trusted where no issuance plausibly
explains the number.

**Each witness alone is worse than useless.** The counts alone fire on
coincidence — 69 gaps sit near a plausible ratio with a tape event of an
entirely different size. The tape alone gives no ratio, only the market's
reading of one.

**This is inference, and it is labelled as such.** Unlike a declared action it
states a ratio nothing filed says outright. What makes it admissible is that two
sources that cannot see each other agree on the same number, in the same window,
in the same direction, at a magnitude neither could confuse with dilution.

**36 gaps hold more than one base change and stay unexplained.** A single filed
ratio covering two actions and an issuance is not separable by rounding, and
choosing one would be choosing the answer. Recrusul is the shape: three base
changes in one gap and years with no filing at all.
