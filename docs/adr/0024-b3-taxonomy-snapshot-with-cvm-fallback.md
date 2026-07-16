# 0024 — B3 taxonomy replaces the five-value sector, snapshot with a CVM fallback

- **Status:** Accepted
- **Date:** 2026-07-15

## Context

The stored analysis classified each ticker with a five-value `Sector` enum
(`bank`, `insurer`, `utility`, `commodity`, `industry`). That was always a proxy:
it drove the front-end's gemstone colour and an *expected-regime* fallback, but
it was too coarse to be the real classification of a company, and it did not
scale to the exchange (ADR 0023 opened on-demand ingestion of any ticker).

B3 groups every listed company in a three-level economic taxonomy —
**setor econômico → subsetor → segmento**. That taxonomy is a **B3 artifact**,
published by the exchange and refreshed weekly. It is *not* in CVM open data:
the CVM registry (cad/FCA) carries only a single `Setor_Atividade` label, and the
FCA securities member's `Segmento` is the *listing* segment (governance level),
not the economic one.

## Decision

Replace the five-value `Sector` as the **stored/served classification** with a
`Classification` value object (`setor`, `subsetor`, `segmento`). Its source is a
**committed snapshot** of B3's Classificação Setorial
(`portfolio/domain/taxonomy.py`, a dict keyed by ticker — reference data, like
`cvm_codes.py`), hand-verified against B3's public tool. A ticker outside the
snapshot degrades to the CVM `Setor_Atividade` as a single level, with
`subsetor`/`segmento` `None` — never an error, never a blank screen.

`TickerAnalysis.classification` is persisted (migration 0009 replaces the
`sector` column with `setor`/`subsetor`/`segmento`) and exposed by the read API.
The front-end shows the three levels and keeps the five gemstone hues as the
visual encoding, mapping a classification to a hue with `gemKey`.

The five-value `Sector` enum **survives internally** as the *regime hint* only:
`StandardizedFinancials.sector` still feeds `expected_regime`, the fallback used
when a filing's regime cannot be read directly. Indicator applicability is
decided by the **filed regime** (ADR 0020), so this internal hint is not on the
correctness path — and leaving it untouched keeps the M1 fidelity gate (#44)
structurally unaffected by this change.

## Consequences

- The UI and the stored data now carry a real, three-level classification;
  KLBN11 reads *Materiais Básicos → Madeira e Papel → Papel e Celulose* instead
  of the flat `industry`.
- The snapshot covers the analysed set today; an on-demand ticker outside it
  still classifies (one level, from CVM) and colours (via `gemKey`). Keeping the
  snapshot current for the whole exchange and regenerating it from B3's file is a
  follow-up, not a blocker.
- The calculator, the regime machinery, and `StandardizedFinancials` are
  untouched: the enum stays exactly where correctness depends on it, so the
  fidelity gate cannot regress from this change.
- Existing persisted rows are backfilled by copying the old `sector` into
  `setor`; a re-run of `analyze` replaces them with the real taxonomy.
- The snapshot is hand-maintained reference data. It can drift from B3's weekly
  refresh; the CVM fallback bounds the blast radius (a stale segment, never a
  crash), and a test pins the covered tickers.
