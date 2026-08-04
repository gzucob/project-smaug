# 0048 — No row before the tape's own first session

- **Status:** Accepted
- **Date:** 2026-08-04

## Context

#164 found TAEE11's cap null for 2015–2016 because its sibling classes
(TAEE3/TAEE4) print no B3 session until 2017 — a unit bundles ON+PN into one
tradable certificate, and the underlying classes can trade loose too, but B3
prints nothing for them until enough free float actually moves outside the
unit. The fix shipped there (`_class_not_yet_traded`) reads B3's own tape
forward from a candidate year to answer "has this symbol started trading yet",
scoped to a sibling class within a multi-class cap.

The same investigation asked the wider question: the *ticker-level* version of
this check (`_not_yet_listed`) has always trusted the CVM FCA's
`Data_Inicio_Listagem` — a floor `docs/adr/…listings.py` already documents as
unreliable enough to need nine hand-curated overrides (`SAPR11`'s date is the
unit's real creation, not the FCA value; `WEGE3`'s is a Novo Mercado migration
date, not WEG's real 1970s debut). Cross-checking that column against B3's own
tape across every ticker CVM lists today (~500) confirms it is wrong at scale,
in both directions:

```
NATU3 (Natura)   FCA=2025-07-01   B3: already trading by 2012-01-02
DASA3            FCA=2021-06-16   B3: already trading by 2012-01-02
EVEN3            FCA=2019-04-02   B3: already trading by 2012-01-02
CRPG3            FCA=1970-12-23   B3: first session 2017-03-29
BALM3            FCA=1970-05-20   B3: first session 2017-07-11
```

Trusting the FCA date as a fallback signal is not safe either — `NATU3` shows
it can be confidently wrong in the direction that would *hide* thirteen years
of real trading behind a false `NOT_YET_LISTED`, which is a worse failure than
the `MISSING_PRICE` it would replace.

`#153` (closing `#63`, extending closed-year coverage from 5 to 10 years)
already hit this once — CXSE3 and SAPR11 both grew rows for years before their
own listing — and chose to keep the row, labelling the price side
`NOT_YET_LISTED` rather than `MISSING_PRICE`. That kept every filed
fundamental visible at the cost of a financial-only row sitting next to a
dozen n/d market cells. `#109`/`#151` move the analysed universe from the nine
curated tickers to the whole registry, where every other ticker trusts the raw
FCA value with no override — the NATU3/CRPG3-shaped failures scale with it.

## Decision

**B3's own tape is the only evidence consulted for "has this security started
trading" — never CVM's FCA, for the ticker itself and not only for a sibling
class.** `_class_not_yet_traded` (`#164`) is generalised and renamed
`_not_yet_traded`; `_not_yet_listed`, `ListedSinceResolver` and the FCA-sourced
`listed_since_resolver` are removed from `AnalyzePortfolioUseCase` outright,
not kept as a fallback — `NATU3` is exactly the shape of failure a fallback
would still let through.

**A year the ticker was not yet trading gets no row at all.** This reopens and
reverses the row-shape half of `#153`'s decision: instead of a row with a
correctly-labelled null price, the closed-year view now has no row for that
year, matching how every reference platform this project is measured against
(Investidor10, StatusInvest, …) presents a pre-listing year — nothing, not a
financial-only card with the market half struck through. The filter runs once,
in `_analyze_ticker`, ahead of the closed-year loop *and* ahead of the growth
and CAGR windows, so a suppressed year is invisible to a later year's
comparisons too — there is no history before the first trading day, for any
purpose, not just for display.

The filter itself reuses one pass over `annuals`, oldest → newest: once a year
is found where the ticker actually priced, every later empty year is a
transient gap (kept, unchanged `MISSING_PRICE` handling) rather than a
pre-listing one. Only an empty year with no earlier priced year yet asks the
tape whether a *later* year ever prices it — the same read `_market_for_year`
already performs for the rows that survive, so this costs nothing new at
exchange scale.

## Consequences

**Gained**: one ground truth for "listed" everywhere the question is asked,
sourced from the same file the price itself comes from (ADR 0041) rather than
a registration date CVM does not keep in sync with actual trading. Scales to
the whole registry without inheriting the FCA's error rate — `#109`/`#151`'s
direction was exactly what made this worth fixing now rather than per-ticker
as each new mismatch surfaces.

**Cost**: a ticker's closed-year history is shorter in the API and the
front-end wherever this fires — CXSE3 loses its 2015–2020 rows entirely, and
SAPR11 its 2015–2016 ones (the *unit* was created in 2017; unlike TAEE11, it
is the younger side of its own bundle). **TAEE11 is not an example of this
decision**: the unit itself has traded since 2006, so it keeps every one of
its rows; only `#164`'s cap-null reclassification touches it, unchanged here.
Nothing computed for a surviving year changes; fewer years are computed at
all for the tickers this does apply to. `smaug doctor`'s coverage denominator
shrinks with it, which is the correct direction: a row that cannot exist is
not a gap to report on.

**Ruled out**: keeping the FCA date as a fallback when the tape has no
opinion. `NATU3` already proves the fallback would occasionally be *worse*
than no check at all, and a source this project does not otherwise trust for
dates (ADR 0035/0042–0044 all made the same call for corporate actions) is not
worth re-admitting here for convenience.

**Not reached**: the `NOT_YET_LISTED` vocabulary value itself is untouched —
it is still what a sibling class's cap-null carries (`#164`). This decision
only removes the *ticker-level* consumer of the FCA date and the row it used
to produce; it does not touch the sibling-class path, which was already
tape-sourced.
