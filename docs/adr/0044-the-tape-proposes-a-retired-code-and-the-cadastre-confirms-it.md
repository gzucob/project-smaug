# 0044 — The tape proposes a retired code, the cadastre confirms it

- **Status:** Accepted
- **Date:** 2026-08-03

## Context

ADR 0042 follows a security through its trading codes using CVM's FCA, which
names the code a registrant has filed for each share class. That column has a
hard floor: it is **empty in every FCA year through 2017**, and CVM publishes one
version per year, so a code retired before its registrant filed a full cycle
under it is named nowhere at all. `KROT3` does not appear in the archive; Cogna's
union of codes is `('COGN3',)`. The consequence was a published null for every
year those securities traded under a name the cadastre forgot.

Three sources were measured before writing a line of it:

- **B3's listed-companies supplement.** `GetDetail` returns `otherCodes`, and it
  carries only what trades today: Engie answers `EGIE3`, Azzas answers `AZZA3`.
  There is no history in it, and a retired root does not resolve at all.
- **CVM's IAN**, the FCA's predecessor, ends in 2009 and cannot reach a 2016
  rename.
- **Matching the company's name across the two sources.** CVM's FCA names each
  registrant per year and B3 prints its own abbreviation of the issuer beside
  each code (`NOMRES`), so the two can be joined inside one year. Validated
  against the years CVM *does* name the code — a labelled set of 1,418 answers —
  the join is **wrong 8.1% of the time** and silent in another 42%. It reads
  "Banco do Estado do Pará" as Banco Pan. On its own it is the trap of #190 with
  a plausible face.

What no source states, the market shows. A rename leaves a signature in the price
series itself: one code prints its last session, and on the very next session
another starts, at the same price. Over the whole window that signature fires 65
times — and it, too, has coincidences. Brookfield's `BISA3` stopped the session
before Celpa's `CELP3` began, 18% apart, two companies with nothing to do with
each other.

## Decision

**The tape proposes and the cadastre confirms.** A predecessor is accepted only
where two independent records agree, neither of which is trusted alone:

1. **The tape.** A code of the same share class whose last session is the one
   immediately before the successor's first, and whose price carries across that
   seam by the same test every other join uses (ADR 0042).
2. **The cadastre.** CVM must have filed that registrant, at some point, under
   the name B3 printed beside the proposed code — compared on the first word,
   which is all two sources that abbreviate differently can share (`ALL AMER LAT`
   against "ALL América Latina Logística"). The names are read from **2010**,
   because the FCA is a snapshot as of each filing: a company that renamed is
   named by the years *before* it did, and nowhere else. That is the same
   property that makes its code column useless here, used the other way round.

The two are also what disambiguates. Codes retire on the same day by coincidence
— Melhoramentos' `MSPA3` printed its last session alongside Tractebel's `TBLE3`,
the day before Engie's `EGIE3` opened — so uniqueness is required of the
**survivors** of both tests, never of the proposals.

This runs only where the cadastre is silent, and its result feeds the same
machinery as any other candidate: the listing floor, the seam test, and the
restatement all apply unchanged.

## Consequences

Nine securities of 506 gain a predecessor the cadastre could not name, and
each is a rename that happened before CVM began recording the code:

```
APER3 <- BRIN3     COGN3 <- KROT3     EGIE3 <- TBLE3
ENEV3 <- MPXE3     FRTA3 <- RNAR3     PRIO3 <- HRTP3
RAIL3 <- RUMO3     WLMM4 <- SGAS4     FICT3 <- ATOM3 <- INET3
```

The last is two hops: Inepar Telecom became Atom, and Atom became Fictor. The
walk is bounded at four, which is twice the deepest chain the exchange has.

`COGN3` gains 2015-2018 outright and its 2019 stops being a 53-session average
of a 248-session year. `EGIE3` 2016 goes from R$38.4792 — four months of the year
— to R$37.1288. `RAIL3` gains 2016.

497 of 506 codes are unchanged, and the coincidences stay out: `CELP3` does not
inherit Brookfield's series, because Celpa was never called Brookfield.

It costs one reduction of the archive. B3's `NOMRES` was not kept when COTAHIST
was collapsed, so the cache format is bumped and every cached year is rebuilt
once — a few minutes on a machine that already holds the ZIPs, and nothing on one
that does not.

What remains uncovered is a rename whose predecessor the market did not price
into the next session — a suspension, a code retired weeks before its successor
opened — and one where CVM never filed the old name. Neither exists in the
current window. A pre-2018 rename is now recovered by two public records
agreeing, and by no hand-written map.
