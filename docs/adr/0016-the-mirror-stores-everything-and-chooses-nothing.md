# 0016 — The raw mirror stores everything and chooses nothing

- **Status:** Accepted
- **Date:** 2026-07-13

## Context

ADR 0006 made CVM the primary ingestion source and called Phase 1 a *faithful,
uninterpreted mirror*. It was not one. `cvm_source._reduce()` made three
interpretive choices at ingestion time, and `cvm_capital._build_index()` made a
fourth:

- keep only the **highest `VERSAO`** — the superseded original filing is gone;
- prefer the **consolidated** statement — the individual one is dropped when both
  exist;
- keep only `ORDEM_EXERC = ÚLTIMO` — the **comparative column**, which CVM ships
  in the same CSV and which describes the prior period, is discarded;
- (FRE) keep only the highest `Versao` of the capital composition.

It also read **4 of the 8** statement members in the ZIP. Not ingested at all:
DMPL, DVA, DRA, and `composicao_capital`.

Each of these is a judgement about what will turn out to matter, made at the
moment we know the least, and it is **irreversible without re-ingesting**: you
cannot re-derive what you never stored. Two of them had already cost us:

- The **DMPL** carries the controllers/minority attribution of net income as a
  matrix (`5.05.01 Lucro Líquido do Período` × `COLUNA_DF`). CXSE3 files that
  split blank in its 2023–24 DRE, which made its net income read 0 (#78). The
  DMPL had the answer — 3,765,184 to the controllers, 0 to minorities — and we
  were not mirroring it.
- **`composicao_capital`** is the only place **treasury shares** are filed
  (BBAS3 22.8m, PETR4 155.8m). Treasury shares are issued but not outstanding,
  and the market cap (ADR 0014) counts them today.

## Decision

**The mirror stores everything and chooses nothing.** One document per
`(ticker, module, reference_date, version, balance_type, ordem_exerc)`. Every
statement member in the ZIP is mirrored — DMPL, DVA and DRA included, whether or
not an indicator reads them yet — and so is every amendment of the FRE.

The selection moves to the **reader**
(`analysis/infrastructure/mongo_fundamentals.py`, `mongo_capital.py`): the
reported period over its comparative, the highest version, the consolidated
statement over the parent-only one. Identical logic, moved — so the numbers do
not change. It can now be revised without another download.

Two mirroring rules follow from the data itself:

- The **DMPL is a matrix, not a list.** Its rows carry an extra `COLUNA_DF` (which
  equity column the figure belongs to), and two rows share a `CD_CONTA`, differing
  only by it. The account mapper preserves it; dropping it would silently collapse
  them into one.
- **`composicao_capital` is mirrored raw, scale problem and all.** Its share counts
  are filed at an **inconsistent scale across companies** — TAEE11, VALE3 and
  CXSE3 file thousands while PETR4, BBAS3 and WEGE3 file units — and the member has
  **no scale column** to tell them apart. So the FRE remains the primary share-count
  source (ADR 0004 stands, and is vindicated); this one is mirrored for its treasury
  figures, and the scale is the reader's problem to solve later.

## Consequences

- **Nothing is silently lost any more.** A question we have not thought to ask yet
  can be answered from the mirror instead of from a re-ingestion.
- **The mirror grows.** Keeping every version, both balance types and the
  comparative multiplies the documents several-fold. It is a local Mongo holding
  nine companies; the cost is nil next to the option value.
- **The reader is where the complexity now lives**, which is the point: choosing
  the amendment over the original is an interpretation, and interpretation is
  Phase 2's job.
- **`smaug doctor` must not move.** This is a refactor of *what is stored*, not of
  *what is computed*: after the wipe and re-ingestion the report reads the same
  `value=1412 named=316 unclassified=0` it did before. A number that moves is a bug.
- **Ingestion is no longer idempotent by accident.** It was append-only before and
  still is, so re-ingesting the same file duplicates its documents; the readers
  break the tie on `fetched_at` within the same version. Pruning stale ingestions
  remains open (#71 covers the Postgres side of the same shape).
- Unblocked, and deliberately **out of scope here**: the comparative column gives a
  free extra year of history (#63); treasury shares in the market cap; the
  `composicao_capital` scale; using the DMPL as a second source for the
  controllers' split (#78 is already fixable from the accounting identity alone).
