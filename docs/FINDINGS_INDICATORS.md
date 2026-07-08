# Findings — indicator fidelity (Phase 2)

A running log of non-obvious findings about how the analysis indicators are
computed and where they diverge from reference platforms (AUVP Analítica,
Investidor10). Recorded so hard-won discoveries are not lost between sessions.

**Convention:** when a session surfaces an important finding (a bug, a data
divergence, a modelling decision), add a dated entry here. A `SessionStart` hook
(`.claude/settings.json`) reminds every session of this convention.

Reference run: `analyze` on **PETR4** and **BBAS3**, CVM mirror through the
2025 closed year (DFP), TTM view priced on the current nominal quote.

---

## F1 — DFC is filed year-to-date while the DRE is filed as isolated quarters

**Status:** latent bug (not yet biting the current numbers).

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

**Status:** latent bug.

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

**Status:** divergence from reference platforms; material for BBAS3.

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

**Status:** divergence from reference platforms.

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

**Status:** missing feature.

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

**Status:** minor gap.

`analyze` calls `compute(current, None, market)` with `previous=None`, so
`revenue_growth` and `net_income_growth` are always null even though `_growth`
is implemented correctly. Wiring a prior comparable period would populate them.

---

## Serving layer — verified faithful

The path calculator → Postgres → FastAPI is a 1:1 passthrough: full `Decimal`
precision is stored (unconstrained `NUMERIC`, no rounding), nulls stay null (DY,
growth, bank-only ratios), and "latest per ticker" ordering by `computed_at` is
correct. So any divergence from the platforms is **upstream** (mapping and
formula definitions above), never in persistence or delivery.
