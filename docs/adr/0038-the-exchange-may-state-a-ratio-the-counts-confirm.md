# 0038 — The exchange may state a ratio, once the counts confirm it

- **Status:** Accepted
- **Date:** 2026-08-02

## Context

ADR 0034 forbids B3's event feed from contributing a **ratio**: "a factor applied
where the counts saw nothing move is how a single split becomes nine (#174)".
The refusal is about evidence, not about the source — and the evidence it asks
for is exactly what a filing gap can supply.

Sabesp is the case that makes it concrete. Its counts move ×5.15652 across one
filing gap and no single ratio explains that, so the chain applied nothing and
**every year of SBSP3's history was 415% out** against the vendor's series. B3's
feed lists three actions in that gap:

```
2025-12-18  BONIFICACAO     2.96470%   x1.029647
2026-03-16  BONIFICACAO     0.16098%   x1.001610
2026-04-28  DESDOBRAMENTO     400%     x5
                                        product = 5.15652
```

The filed counts moved 683,509,869 → 3,524,534,028 = **5.15652**. Two records
that cannot see each other state the same move, to five decimals.

## Decision

**A gap's ratio may be read as the product of every action B3's feed lists inside
it, accepted only where that product reconciles with the move the counts made**
(within 2%, the same margin ADR 0037 allows for what else changed hands in a
filing year).

It outranks the tape's reading (ADR 0037) and is outranked by everything above
it: a declared CVM action, a clean filed ratio, the composition (ADR 0028). Its
factors are exact rather than rounded to a plausible fraction, and they carry
their own dates — so it is also the only reading that handles a gap holding
*several* actions, which no rounding of a single ratio can.

**The window a gap searches is fixed at the same time.** It now opens the year
*after* the earlier filing and closes the year after the later one:

- not the earlier filing year, because an action inside it is already in that
  filing's own count — reaching back offered LREN3's September 2015 split and
  RENT3's November 2017 split to gaps whose counts had already absorbed them;
- and it spans the whole gap, however long: Recrusul files nothing for 2023 or
  2024, so one gap runs four years and holds four base changes. A window seeing
  only the last of them matched it to the move the other three made — measured,
  and it was the one real regression this change first produced.

## Consequences

Over the whole exchange, before and after, on the 5,544 stored views:

```
identical                     264,944
<= 0.05% (source rounding)      1,948
<= 1%                             198
> 1%                              894      20 codes
value -> null                       0
```

**SBSP3 goes from 415.65% error to 0.00% in all eleven years.** It is a company
anyone opens, and its whole price history was five times too high.

Against the vendor's back-adjusted series: **128 better, 24 worse.**

**All 24 are the witness being stale, not the chain being wrong**, and this is
the finding worth writing down. They are CYRE3 (11 years), RENT3 (11), DXCO3 and
VBBR3. Each is a corporate action of late 2025 that three independent records
confirm and Yahoo has not applied:

```
CYRE3   feed 1.1895833   counts 1.18958   tape EB on 2026-01-02, dist 142->143
RENT3   feed 1.0384610   counts 1.03846   tape EJB on 2025-12-30, dist 204->205
DXCO3   feed 1.1200000   counts 1.12000
VBBR3   feed 1.0711024   counts 1.07110
```

Yahoo's `adjclose` equals its `close` on every session around CYRE3's event —
it applied no adjustment at all. So a vendor series is **not a witness for an
action inside the last year**, and any measurement that treats it as one will
read a correct restatement as a regression.

**That cuts against ADR 0037's stated evidence for its size floor.** The floor
was justified by CYRE3 going "from an exact match to 16% out" — which is this
staleness, not an error. The floor may still be right for a rule that *rounds* a
ratio rather than reading it, and this one does not need it; but the reasoning
behind it has to be re-measured against a witness that is current. Filed as an
issue rather than settled here.

**The refusal in ADR 0034 stands where it was aimed.** A feed factor still
contributes nothing where the counts saw nothing: Oi's counts move ×5 across a
gap that also holds a 1:10 grupamento — a debt conversion rode along — and no
product of factors is that move, so the gap keeps its factor of 1.
