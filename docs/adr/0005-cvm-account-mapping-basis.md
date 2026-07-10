# 0005 — Standardized financials map to the controllers' share, with DFC flows on their own period span

- **Status:** Accepted
- **Date:** 2026-07-08
- **Provenance:** recorded retroactively on 2026-07-10 from F1–F6 of
  `docs/FINDINGS_INDICATORS.md` (deleted; recoverable in git history).
  See `.claude/RULES/RULES_DOCS.md`.

## Context

The first end-to-end run of `analyze` on PETR4 and BBAS3 produced indicators
that were internally consistent and systematically wrong against AUVP Analítica
and Investidor10. Six causes were found, all in how CVM accounts are read into
`StandardizedFinancials` — none in the formulas.

**The two statements are filed on different period spans.** In the ITR mirror,
the income statement (DRE) arrives as **isolated three-month quarters**
(`period_start` = Apr 1 for Q2). The cash-flow statement (DFC) arrives
**accumulated year-to-date** (`period_start` = Jan 1 for every quarter). PETR4's
2025 depreciation reads 18.9 / 39.9 / 62.3 / 84.4 bn across Q1–FY — a running
total, not four quarters. The TTM isolation used the DRE's span for both, so
year-to-date depreciation was treated as an isolated quarter. It happened to
telescope back to the right annual figure *only because* the newest period was
the annual DFP; the moment the newest period is a mid-year quarter, TTM EBITDA
and everything downstream of it would be wrong.

**Consolidated figures include the minority interest; the platforms do not
use them.** BBAS3's fiscal-year 2025 net income is 16.78 bn consolidated, of
which 3.08 bn (~18%) is non-controlling. Both numerator and denominator being
consolidated kept ROE self-consistent while sitting 2–3 points above the
platforms. The two tickers expose the split differently: PETR4 files a
consolidated total (`2.03`) with a minority sub-line (`2.03.09`); BBAS3 files an
explicit "Patrimônio Líquido Atribuído ao Controlador" (`2.07.01`).

**The primary net-income name match never fired.** The needle was
`"lucro/prejuizo consolidado do periodo"`; CVM's actual label is "Lucro **ou**
Prejuízo **Líquido** Consolidado do Período". The code silently fell through to
"das operações continuadas" (`3.09`). Harmless in a year with no discontinued
operations — an understatement in any year with one, and invisible either way.

**Net-debt cash was too narrow.** Cash was `1.01.01` alone; the platforms also
count `1.01.02` (short-term financial investments). PETR4's net debt read
R$ 348.4 bn against a reported ~R$ 300 bn, pushing NetDebt/EBITDA to 1.51 where
the platforms show ~1.0.

**Dividend yield was hard-coded to null** because brapi's free plan does not
expose trailing dividends — yet the DFC's financing section already carries
dividends and JCP paid to controllers (PETR4 `6.03.05`, BBAS3 `6.03.04`).

**Growth was never computed.** `analyze` called `compute(current, None, market)`
with `previous=None`, so `revenue_growth` and `net_income_growth` were always
null even though `_growth` was correct.

## Decision

- **DFC-sourced flows isolate on their own span.** `dfc_period_start` is tracked
  separately from the DRE's `period_start`. `dep_amort`, `dividends_paid`, `cfo`
  and `capex` are isolated on the DFC's year-to-date span; DRE flows on the
  quarterly one.

- **Equity and net income use the controllers' share.** Prefer the explicit
  controllers' line when the filer publishes one; otherwise
  `consolidated − minority`. `_controllers_share` in `mongo_fundamentals.py`.

- **Net income targets the controllers' line directly**, rather than matching a
  consolidated label and hoping. `3.09` ("operações continuadas") survives only
  as an explicit fallback when the controllers' line is absent.

- **Cash for net debt is `1.01.01 + 1.01.02`.** Banks and insurers skip net debt
  entirely, so this affects only the standard regime.

- **Dividends come from the DFC**, not from the price source: financing outflows
  whose name mentions dividendo/JCP and "pag", **excluding** the "não
  controladores" line. `dividend_yield` = trailing-twelve-month dividends /
  market cap.

- **Growth compares the TTM against the prior closed year** (the DFP for the
  year before the TTM's end year) — a clean year-over-year when the TTM ends in
  December.

## Consequences

- The indicators moved to where the platforms are. PETR4: net debt 348.4 → 333.4
  bn, NetDebt/EBITDA 1.51 → 1.45, DY 8.6% now populated. BBAS3: ROE 8.7 → 7.2%
  (controllers), P/L 6.7 → 8.2.

- **Two period spans now coexist in one entity.** `StandardizedFinancials`
  carries both `period_start` and `dfc_period_start`, and any new flow must
  declare which statement it came from. Getting this wrong is silent: the number
  is plausible and off by a factor that depends on which quarter you look at.

- **The controllers' share is a per-filer negotiation, not a code lookup.** The
  fallback (`consolidated − minority`) exists because filers disagree about
  whether to publish the line at all. A new ticker can arrive filing it a third
  way, and the failure will be a slightly-wrong ROE, not an exception.

- **Name matching is load-bearing and fragile.** F2 was a dead needle that no
  test caught, because the fallback produced a correct number for the year it
  was tested on. Any account matched by label rather than code carries this
  hazard — the same one that leaves capex null in some older filings
  (ADR 0002, #41).

- Growth **degrades to null** when the prior year's DFP is not in the mirror,
  rather than comparing against an unrelated period. A ticker's first ingested
  year has no growth figure, by construction.
