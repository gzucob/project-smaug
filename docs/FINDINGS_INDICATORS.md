# Findings — indicator fidelity (Phase 2)

A running log of non-obvious findings about how the analysis indicators are
computed and where they diverge from reference platforms (AUVP Analítica,
Investidor10). Recorded so hard-won discoveries are not lost between sessions.

**Convention:** when a session surfaces an important finding (a bug, a data
divergence, a modelling decision), add a dated entry here. A `SessionStart` hook
(`.claude/settings.json`) reminds every session of this convention.

Reference run: `analyze` on **PETR4** and **BBAS3**, CVM mirror through the
2025 closed year (DFP), TTM view priced on the current nominal quote.

**All findings (F1–F6) resolved.** After the change — PETR4: DY 8.6%, net debt
348.4→333.4 bn, ND/EBITDA 1.51→1.45, revenue/NI growth now populated; BBAS3: ROE
8.7→7.2% (controllers), P/L 6.7→8.2, DY 5.9% (growth null until its 2024 DFP is
ingested).

---

## F1 — DFC is filed year-to-date while the DRE is filed as isolated quarters

**Status:** FIXED — DFC flows now isolate on `dfc_period_start`.

The income statement (DRE) in the ITR mirror comes as **isolated 3-month
quarters** (`period_start` = Apr 1 for Q2, Jul 1 for Q3). The cash-flow
statement (DFC) comes **accumulated year-to-date** (`period_start` = Jan 1 for
every quarter).

Evidence (PETR4 2025 DFC "Depreciação"):

| Period | DFC start | Depreciation (R$ thousand) |
|---|---|---|
| Q1 (Mar 31) | 2025-01-01 | 18,976,000 |
| Q2 (Jun 30) | 2025-01-01 | 39,928,000 (6-month YTD) |
| Q3 (Sep 30) | 2025-01-01 | 62,317,000 (9-month YTD) |
| FY (Dec 31) | 2025-01-01 | 84,388,000 |

`dep_amort` is pulled from the DFC but the TTM isolation in
`ttm._isolate_year` uses the DRE's `period_start` (3-month), so YTD depreciation
is treated as an isolated quarter. It currently "telescopes" back to the correct
annual figure **only because** the newest period for both tickers is the annual
DFP, making the TTM window equal to calendar year 2025. The moment the newest
period is a mid-year quarter, TTM EBITDA (and EV/EBITDA, EBITDA margin,
NetDebt/EBITDA) will be wrong.

**Fix direction:** track a separate `dfc_period_start` and isolate DFC-sourced
flows (`dep_amort`, and future `dividends_paid`) on their own span. This is also
a prerequisite for sourcing dividends from the DFC (see F5).

---

## F2 — The primary net-income name match is dead; a silent fallback is used

**Status:** FIXED — net income now targets the controllers' line directly.

`mongo_fundamentals._NET_INCOME_NAMES` tries first
`"lucro/prejuizo consolidado do periodo"`. The real CVM label is
**"Lucro ou Prejuízo Líquido Consolidado do Período"** (BBAS3) — the `" ou "`
and `"Líquido"` mean the primary needle never matches, so the code silently
falls through to `"...das operações continuadas"` (code 3.09).

For 2025 this happens to be harmless (no discontinued operations, so 3.09 = 3.11
consolidated). But with any discontinued operation the fallback **understates**
net income. The intended primary match is effectively broken and unnoticed.

**Fix direction:** match net income robustly and, per F3, target the
controllers' line directly.

---

## F3 — Equity / net income use the consolidated figure (incl. minority)

**Status:** FIXED — equity and net income now use the controllers' share.

The mapper pulls consolidated equity (2.03 for a normal company, 2.07 for a
bank) and consolidated net income — both **including** the non-controlling
(minority) interest. AUVP / Investidor10 use the **controllers'** figure.

Evidence — BBAS3 FY2025 net income (R$ thousand):

| Line | Value |
|---|---|
| 3.11 Consolidated (used today) | 16,781,938 |
| 3.11.01 Attributed to controllers | 13,698,124 |
| 3.11.02 Minority | 3,083,814 (**~18%**) |

Because both numerator and denominator are consolidated, ROE is internally
consistent, but it sits ~2–3 percentage points above the platforms. For BBAS3
the minority slice is large enough to matter. Note the two tickers expose the
split **differently**:

- **PETR4:** consolidated total (2.03) + minority sub-line (2.03.09) →
  controllers = total − minority.
- **BBAS3:** an explicit "Patrimônio Líquido Atribuído ao Controlador" (2.07.01)
  and the same for net income (3.11.01).

**Fix direction:** prefer the explicit controllers' line; otherwise
`consolidated − minority`. Apply to equity and net income so ROE, P/E, P/B and
net margin line up with the platforms.

---

## F4 — Net-debt cash is too narrow (excludes short-term investments)

**Status:** FIXED — cash now includes 1.01.02.

`net_debt = total_debt − cash`, but `cash` is only 1.01.01 (Caixa e
Equivalentes). Platforms also count 1.01.02 (short-term financial investments).
Excluding it **inflates** net debt.

Evidence — PETR4 FY2025 (R$ thousand): 1.01.01 = 35,608,000; 1.01.02 (Aplicações
Financeiras) = 15,000,000. Persisted net debt was **R$ 348.4 bn** vs Petrobras'
reported ~R$ 300 bn, which pushes NetDebt/EBITDA to 1.51 (platforms ~1.0) and
EV/EBITDA up accordingly.

**Fix direction:** cash = 1.01.01 + 1.01.02. (Banks skip net debt entirely, so
this only affects non-financials.)

---

## F5 — Dividend Yield is not computed, but the data exists in the DFC

**Status:** FIXED — DY sourced from DFC dividends/JCP paid to controllers.

`brapi_price` hard-codes `dividends_12m = None` (the free brapi plan does not
expose trailing dividends), so `dividend_yield` is always null. However, the CVM
DFC financing section already carries dividends/JCP paid to controllers:

- **PETR4:** 6.03.05 "Dividendos pagos a acionistas da Petrobras" = −45,205,000.
- **BBAS3:** 6.03.04 "Dividendos ou juros sobre o capital próprio pagos aos
  acionistas controladores" = −6,680,889.

**Fix direction:** extract dividends/JCP paid (financing outflows whose name
mentions dividendo/JCP + "pag", excluding the "não controladores" line), carry
it as a TTM flow (isolated on the DFC span — see F1), and compute
DY = trailing-12m dividends / market cap.

---

## F6 — Revenue / net-income growth are never computed

**Status:** FIXED — growth compares the TTM against the prior closed year (DFP).

`analyze` used to call `compute(current, None, market)` with `previous=None`, so
`revenue_growth` and `net_income_growth` were always null even though `_growth`
is implemented correctly. Now the use case reads all annual DFPs and passes the
year-before the TTM's end year as the comparison base (clean YoY when the TTM
ends in December). It degrades to null when that prior year was not ingested —
e.g. BBAS3, whose 2024 DFP is not in the mirror yet (data follow-up: ingest it).
PETR4 (with the 2024 DFP present) reports revenue growth 1.4%, net-income
growth 200.8% off a depressed 2024 base.

---

## F7 — Closed-year multiples now use the annual DFP, not an annualized quarter (2026-07-08)

**Status:** MODELLING DECISION — Stage 4 (two views side by side).

`analyze` now emits both views per ticker: the live TTM **and** one closed-year
row per ingested DFP, tagged by a `view` discriminator (`ttm_live` /
`closed_year`). The closed-year row reuses the PR #8 pricing trick — reprice the
current market cap onto the year's dividend-adjusted average
(`current_cap × adjusted_avg / current_price`) — but the **net-income basis
changed**: PR #8 computed the historical multiple from the *latest ITR quarter
annualized*, whereas Stage 4 uses the **true annual DFP** figure (12-month, no
annualization, controllers' share).

Evidence — PETR4 FY2024 closed year, verified end-to-end:

| Figure | Value | Reference |
|---|---|---|
| Adjusted-average price | R$ 30.48 | matches AUVP basis exactly |
| Nominal-average price | R$ 38.20 | stored for reference |
| P/L (closed year) | **11.00** | AUVP ≈ 11.44 |
| P/VP | 1.10 | AUVP ≈ 1.14 |

The residual P/L gap (11.00 vs 11.44, ~4%) is the net-income basis: the actual
annual DFP earnings attributed to controllers vs PR #8's annualized-quarter
proxy. The DFP figure is the intended, more faithful basis for a *closed year*,
so this is a deliberate improvement, not a regression. Note the closed-year DY
uses that year's dividends over the repriced cap, so PETR4 2024's extraordinary
payout shows a very high DY (~24.9%) — expected for that year, but flagged as a
number to sanity-check against the platforms when more years are ingested.

---

## F8 — Extended indicator set: per-share, extra multiples, ROIC and FCF (2026-07-08)

**Status:** MODELLING DECISION — new indicators added without new ingestion.

The indicator set grew from 14 to 29 to approach the coverage of AUVP /
Investidor10. Every new indicator is derived from the accounts already mirrored
— **no new ingestion was needed**, including the free-cash-flow family, because
the whole DFC (cash-flow statement) is already stored (it is where `dep_amort`
and `dividends_paid` come from). Modelling choices worth recording:

- **Per share (LPA/`eps`, VPA/`bvps`)** use brapi's current `sharesOutstanding`.
  For the **closed-year** view this is the *current* share count applied to a
  past year's earnings/equity, not the period-end count (we do not have a
  historical share series). Absolute LPA/VPA for old years are therefore
  approximate; the *multiples* (P/L, P/VP) stay faithful because they reprice the
  market cap, not the share count. Flag to revisit if a historical share count
  becomes available.
  > **Superseded by F12 (2026-07-09).** The historical share count *is*
  > available — CVM's FRE publishes it per year — so each view now divides by
  > the count filed for its own fiscal year. The approximation described above
  > no longer applies.
- **ROIC** uses NOPAT = annualized EBIT × (1 − 0.34), a flat statutory rate
  (IRPJ 25% + CSLL 9%) rather than each company's effective rate — a deliberate
  simplification matching how the platforms present it. Invested capital =
  controllers' equity + net financial debt. Financials return null (net debt is
  not defined for them).
- **Capex** is extracted from the DFC investing section (codes `6.02.*`) as the
  **outflows** (negative amounts) whose label mentions `imobilizado` or
  `intangível`; disposals (positive inflows) are ignored. This is *gross* capex.
  `fcf = CFO − capex`, annualized like the other flows and isolated on the DFC
  span in the TTM (same mechanism as F1). Null when either leg is missing, so FCF
  degrades rather than misleads.
- **P/working-capital** (`price_to_working_capital`) can go negative when current
  liabilities exceed current assets — that is meaningful (Graham's basis), not a
  bug, so it is left signed.
- **Payout** = trailing dividends paid / net income over the *same* period basis
  (both trailing-12m in the TTM, both annual in a closed year); not annualized,
  since numerator and denominator share the span.
- **Headline financials** (`revenue`, `net_income`, `dividends`) are persisted
  alongside the ratios — the period's own absolute figure in reais (TTM sum or
  annual DFP), *not* annualized. They are not "indicators" but are stored on the
  same row (like `net_debt`/`fcf`) so the front-end can chart the per-year
  evolution of revenue/earnings/dividends, which the ratios alone cannot
  reconstruct. `dividends` is the same DFC-sourced figure that backs `payout`
  and the DY.

A runtime verification run (2026-07-09) confirmed the new indicators flow
end-to-end (migration 0004 applied, `analyze` re-run for PETR4/BBAS3, read API +
front-end) — most populated correctly; see **F9** for the two remaining data
gaps. No AUVP/Investidor10 delta cross-check has been done yet — verifying ROIC,
FCF and the per-share figures against the platforms remains the open follow-up.

---

## F9 — Runtime verification of PR #19: LPA/VPA null + closed-year FCL gap (2026-07-09)

**Status:** LPA/VPA RESOLVED by F12 (the fix is not the one proposed below —
`market_cap / price` turned out to be biased). The closed-year FCL gap is still
open.

PR #19 was exercised end-to-end (migration 0004, `analyze` for PETR4/BBAS3, API
and front-end). Most new indicators populated correctly — e.g. PETR4 TTM: ROIC
12.3%, EBIT margin 28.9%, PSR 1.05, payout 37.4%, FCL R$ 85.8 bn, FCF yield
16.4%; revenue / net income / dividends charted across 2021–2025. Two gaps
remain, both in the *data*, not the formulas:

- **LPA (`eps`) and VPA (`bvps`) are null for every ticker and view.** brapi's
  free-plan quote returns `marketCap` but not `sharesOutstanding`, so
  `MarketData.shares` is `None` and the per-share formulas degrade to null. Fix
  (issue #22): derive `shares = market_cap / price` (exact, since market cap ≡
  price × shares) as a fallback in `BrapiPriceProvider`. This mirrors F5 (DY was
  also missing from brapi and had to be sourced elsewhere).
- **Closed-year FCL is null for some years** ("série insuficiente" in the year
  chart), because `_capex` (DFC `6.02.*` PP&E/intangible outflows) doesn't match
  the label/code in some older DFP filings. TTM FCL is fine. Verify the capex
  match against multi-year DFPs (noted as related in #22).

---

## F10 — ROE/ROA divide by the closing balance, not the period average (2026-07-09)

**Status:** OPEN QUESTION — no divergence measured yet.

Surfaced while writing the per-indicator reference docs for the front-end
(`frontend/src/lib/indicator-docs.ts`), which forced every formula to be stated
exactly as `calculator.py` computes it.

`roe = annualized net income / f.equity` and `roa = annualized net income /
f.total_assets` both use the **closing** balance of the period. A stock taken at
a single instant is being compared against a flow earned across the whole
period. Where equity moved a lot during the year (large issuance, buyback,
or the dividend payment itself), the average balance would be the more faithful
denominator, and some references compute it that way.

This has **not** been checked against AUVP / Investidor10 for our tickers — it
is recorded as a known modelling choice, not as a confirmed error. Measuring it
needs the opening balance (the prior year's DFP), which
`StandardizedFinancials` already reaches for via `_prior_year_annual`.

The same reasoning applies to `asset_turnover` (annualized revenue over closing
total assets).

### Maintenance hazard introduced by the docs

`indicator-docs.ts` now states each formula in prose, in a second place, in
another language. It is documentation of `calculator.py`, and nothing enforces
the correspondence. **When a formula changes in `calculator.py`, update the
matching `formula` (and `caveat`) entry.** The `naSectors` field mirrors the
`is_financial` guards in the same file.

---

## F12 — brapi's market cap is company-wide, so `cap / price` cannot yield a share count (2026-07-09)

**Status:** RESOLVED — share counts now come from CVM's FRE (issue #22).

Issue #22 proposed deriving the missing share count as `market_cap / price`,
calling it "exact, since market cap ≡ price × shares". Probing the live brapi
quote disproved the premise: **brapi returns the same company-wide `marketCap`
for every class of a company's shares.** PETR3 and PETR4 both report
R$ 539,240,668,702.99, at prices of R$ 43.53 and R$ 39.21 — dividing that one
cap by each price yields 12.39 bn and 13.75 bn shares, and neither is the real
12.89 bn. The identity only holds for a single-class ticker.

Measured bias against the counts CVM actually has on file:

| Ticker | `cap / price` | CVM filed (2025) | Error |
|---|---|---|---|
| PETR4 | 13.75 bn | 12.89 bn | +6.7% (understates LPA/VPA) |
| BBDC4 | 9.74 bn | 10.59 bn | −8.0% (overstates LPA/VPA) |
| VALE3 | 4.14 bn | 4.44 bn | −6.7% |

**The multiples were never affected**: `pe = cap / net_income` and
`pb = cap / equity` are computed from the market cap directly and never touch
`shares` (`calculator.py`). Only the absolute per-share values were at risk.

### Where the share count actually lives

Not in the statements. BPA/BPP/DRE/DFC carry no share-count account, so no
amount of remapping would have found it. CVM publishes it in the **FRE**
(*Formulário de Referência*), a yearly ZIP whose
`fre_cia_aberta_capital_social_*.csv` lists each company's capital. The
`Capital Integralizado` (paid-in) row is the one that reflects shares in
existence; the file also carries `Capital Emitido` / `Capital Subscrito`, which
differ and must be ignored.

Consequences, all now implemented:

- The FRE is keyed by **CNPJ**, not the `CD_CVM` the statements use — hence the
  second map in `portfolio/domain/cvm_codes.py`, cross-checked against CVM's
  `cad_cia_aberta.csv` registry.
- pycvm's `FREFile` cannot read the modern files (`BadDocument: unknown document
  type 'FRE WEB'`), so `cvm_capital.py` reads the CSV member directly. This is
  the second pycvm quirk in this codebase, after the DMPL crash.
- One ZIP **per year** (2021–2026) means a real historical series, so each
  closed-year view now divides by the count filed *for that year*. This retires
  the F8 approximation. The series is visibly right: PETR4 goes 13.04 bn
  (2021–23) → 12.89 bn after the cancellations; VALE3 5.00 → 4.44 bn.
- The DRE *does* carry a reported LPA per class (`3.99.01.01` ON /
  `3.99.01.02` PN), which would be even more faithful. It was rejected as the
  primary source because coverage is uneven: **BBAS3 files it as zero** and
  **VALE3 files its value in the PN slot** despite having only ON shares.

### Known residue

- **Units (SAPR11, TAEE11) keep `eps`/`bvps` null.** A unit is a bundle of
  shares (SAPR11 = 1 ON + 2 PN — its filed counts are exactly 1:2), so the quoted
  price is the bundle's while CVM counts the underlying shares. Dividing by the
  share count would produce a figure that does not line up with the price.
  Tracked separately; see `portfolio/domain/share_classes.py`.
- **BBAS3 filed `0` shares for 2023** and **VALE3 has no 2023/2024 FRE** in the
  mirror. `MongoSharesReader` drops non-positive counts and falls back to the
  nearest *earlier* year, so those views use an adjacent year's count.
- **Counts are as filed, not split-adjusted.** BBAS3 shows 2.87 bn through 2022
  and 5.73 bn from 2024 (the 2:1 bonus). A closed-year LPA chart therefore has a
  genuine step at the split — faithful to what was reported that year, but not
  what a split-adjusted platform series shows.
- brapi's `cap / price` survives as a **fallback** in `BrapiPriceProvider` for a
  ticker with no FRE row at all. It is biased as measured above; the fallback is
  logged when used.

---

## Serving layer — verified faithful

The path calculator → Postgres → FastAPI is a 1:1 passthrough: full `Decimal`
precision is stored (unconstrained `NUMERIC`, no rounding), nulls stay null (DY,
growth, bank-only ratios), and "latest per ticker" ordering by `computed_at` is
correct. So any divergence from the platforms is **upstream** (mapping and
formula definitions above), never in persistence or delivery.
