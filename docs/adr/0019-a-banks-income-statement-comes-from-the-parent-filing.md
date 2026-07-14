# 0019 — A bank's income statement comes from its parent filing, its balance sheet from the consolidated one

- **Status:** Accepted
- **Date:** 2026-07-14

## Context

CVM receives two versions of every statement: the parent-only one (`_ind`) and the
consolidated one (`_con`). For a non-financial filer they agree where it matters — the
consolidated statement's controllers' share *is* the parent's bottom line, as the
accounting identity requires (PETR4 2024: R$36.6 bn on both sides). The reader has
always preferred the consolidated one, and for those seven tickers nothing turns on it.

A bank is different. The two filings are drawn under **different accounting standards**
(BACEN and IFRS), and they materially disagree:

| BBAS3, 2024 | bottom line | attributed to controllers |
|---|---|---|
| parent (`DRE_ind`) | **R$ 35.3 bn** | — (no split filed) |
| consolidated (`DRE_con`) | R$ 29.2 bn | **R$ 26.4 bn** |

The bank reports 35.3. The press reports 35.3. The reference platforms' LPA divides
35.3 (Dados de Mercado: 6.15 for 2024). Nobody publishes the consolidated figure. The
same holds for BBDC4 — its published accounting result is the parent's R$19.1 bn, not
the consolidated R$17.3 bn. Reading the consolidated statement put our net income **25%
low for BBAS3 and 10% low for BBDC4**, and every indicator built on it with them.

The **balance sheet runs the other way**: there the market reads the consolidated
statement. Bradesco's published total assets are the consolidated R$2.07 tn, not the
parent's R$1.69 tn. Equity, meanwhile, barely differs between the two (BBAS3: 179.6
against 180.9).

## Decision

For a filer whose **detected regime is `BANK`** (ADR 0015 — read off the filing, never
the sector), the **income statement** is taken from the parent filing. Every other
statement — balance sheet and cash flow — stays consolidated, for banks and for
everyone else.

The parent statement's bottom line (`Lucro ou Prejuízo Líquido do Período`) outranks the
"operações continuadas" fallbacks when the total is chosen: those sit *above* the
employees' profit share, and BBAS3's parent 2024 reads R$39.8 bn there against R$35.3 bn
at the bottom.

## Consequences

- The banks' results now equal what they publish, every year in the mirror (BBDC4
  2021–2025: 21.9 / 20.7 / 15.1 / 19.1 / 24.5). BBAS3's 2024 net margin and P/E land on
  the reference platform's values exactly.
- We deliberately mix two statements for one filer. That is the filings' asymmetry, not
  ours — and it is the only way to reproduce the numbers the market actually quotes.
- ROE for a bank divides a BACEN result by an IFRS equity. The two equities differ by
  under 1%, so the mixture costs less than either pure basis would.
- The seven non-financial tickers are untouched: their two statements agree.
- An insurer (BBSE3, and CXSE3 which files as a holding) is *not* covered by this. Their
  consolidated filings already carry the published result, and no evidence was found that
  they file two disagreeing versions. If one ever does, this ADR is the precedent, not
  the rule to stretch.
