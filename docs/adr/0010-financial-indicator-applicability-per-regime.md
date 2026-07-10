# 0010 — Financial-indicator applicability is decided per accounting regime

- **Status:** Accepted
- **Date:** 2026-07-10

## Context

ADR 0008 gave every null a `NullReason` but deliberately left one verdict open
(its own last consequence, and #30): of the indicators the calculator nulls for
a financial filer, *which* are genuinely inapplicable versus merely unmapped?
Until decided, all of them were attributed `inapplicable_regime` by a single
regime-blind set (`_REGIME_GUARDED`) covering both banks and insurers — so Smaug
called "impossible" what was often just "not implemented yet", and hiding all
nulls (the WEB toggle, #33) would have concealed real coverage gaps.

The audit had to be evidence-based, not from memory (#30). We captured the two
reference platforms for all four portfolio financial tickers — banks BBAS3 /
BBDC4, insurers BBSE3 / CXSE3 — via WebFetch (Investidor10, public) and
screenshots (AUVP Analítica, login-gated). The captures live under `tmp/`
(gitignored); a committed tolerance fixture remains #44.

Two findings drove the decision:

- **Banks and insurers are near-mirror images**, so a single `is_financial`
  verdict is wrong. AUVP omits net debt / Dív.Líq-PL / EV-EBITDA for banks and
  shows *Índice de Basileia* instead — but shows all of them for insurers
  (they hold investment portfolios). Conversely, both platforms compute banks'
  gross/EBIT margins, but show insurers' margins as a degenerate 0%.
- **"Shown" is not "meaningful."** Investidor10 runs a generic corporate
  template over the regulated filings, yielding artifacts (0% insurer margins,
  a 63% ROIC for BBSE3, a bank "gross margin" though a bank has no gross-profit
  line). AUVP is the tiebreaker. Two indicators stayed a judgement call
  (banks' margins, banks' current ratio — where AUVP ~10 and Investidor10 ~1
  disagree tenfold), resolved with the portfolio owner: if any reference
  computes it, treat it as computable (unmapped), reserving inapplicable for
  what no platform computes and the schema makes meaningless.

## Decision

Applicability keys on the **expected accounting regime** (bank / insurance),
not on a blanket `is_financial`. A per-regime `_INAPPLICABLE_BY_REGIME` map in
`calculator.py` names only the *genuinely inapplicable* indicators; every other
null a financial filer produces falls through to the input check and is
attributed `source_account_unmapped` (cause 2), pending #48.

- **Bank — inapplicable:** `net_debt`, `net_debt_to_ebitda`, `debt_to_equity`,
  `ev_ebitda`, `ebitda_margin`. (Capital adequacy is Basileia; a bank has no
  EBITDA.) Everything else it nulls — `gross_margin`, `ebit_margin`, `roic`,
  `current_ratio`, `price_to_ebit`, `price_to_working_capital` — is unmapped.
- **Insurer — inapplicable:** `gross_margin`, `ebit_margin`, `ebitda_margin`
  (degenerate on both platforms). Everything else it nulls — `net_debt`,
  `net_debt_to_ebitda`, `debt_to_equity`, `ev_ebitda`, `roic`, `current_ratio`,
  `price_to_ebit`, `price_to_working_capital` — is unmapped.

This ADR changes **only attribution**. The calculator still nulls these values
(their inputs are unmapped), so indicator values are byte-identical; only the
reason moves from `inapplicable_regime` to `source_account_unmapped` for the
reclassified cells. The `unexpected_regime` override (ADR 0008) is unchanged: a
filer whose detected regime differs from its sector's still overrides both.

## Consequences

- `smaug doctor` now distinguishes a bank's true "never" (Basileia-style) from
  its "not yet" (margins, ROIC), and the WEB toggle (#33) can hide the former
  without hiding the latter.
- The actual computation of the now-`source_account_unmapped` indicators is
  #48 (map bank/insurer line items) + #27 (their indicator set). Once #48 maps
  the accounts, those cells light up with no calculator change, because the
  `is_financial` value-guard only suppresses inputs, not the classification.
- The verdicts are pinned by `test_calculator.py` (bank and insurer cases). A
  new financial indicator must be placed in — or deliberately left out of — the
  per-regime set, or it defaults to unmapped.
- The audit rests on one snapshot of two platforms for four tickers. It is not
  the committed fidelity fixture (#44); a future re-capture uses the same `tmp/`
  workflow. Banks' current ratio is knowingly labelled unmapped despite a 10x
  cross-platform disagreement — #48 must decide how (or whether) to derive it
  from a bank balance sheet that has no clean current/non-current split.
