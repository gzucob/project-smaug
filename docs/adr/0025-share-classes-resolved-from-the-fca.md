# 0025 — Share classes for the cap are resolved from the FCA

- **Status:** Accepted
- **Date:** 2026-07-15

## Context

The market cap sums a company over its listed share classes, each at its own
price (ADR 0014): `cap = Σ class_price × class_count`. Which classes a ticker's
company lists was hand-curated in `LISTED_CLASSES` for the nine portfolio
tickers. For any other ticker `listed_classes` returned empty, so the cap — and
every multiple that divides by it (P/L, P/VP, PSR, EV/EBITDA, DY, …) — was a
`missing_share_count` null. On-demand ingestion (ADR 0023) made that the norm:
KLBN11 and ITSA4 ingested and computed their accounting ratios, but showed no
valuation at all.

The classes are in the CVM FCA that the company registry already reads. The
securities member lists each `Codigo_Negociacao` with its `Valor_Mobiliario`
type (Ações Ordinárias / Preferenciais / Units). Two real shapes occur:

- most companies file the ON/PN tickers directly (ITSA3 + ITSA4);
- some file **only the unit** — Klabin lists KLBN11 and never KLBN3/KLBN4 — but
  the unit row spells its bundle out in `Composicao_BDR_Unit` ("1 KLBN3 + 4 KLBN4").

## Decision

Resolve a ticker's ON/PN classes from the FCA, exposed on `CompanyIdentity.
share_classes` and injected into the analysis as a `classes_resolver` (curated
`LISTED_CLASSES` for the nine, FCA-derived for the rest). `capitalize` takes the
resolved classes instead of looking them up by ticker.

Two parsing rules:

- **Kind by label, then by unit composition.** `Ações Ordinárias` → ON,
  `Ações Preferenciais` → PN. For a unit-only filer, parse the class tickers out
  of `Composicao_BDR_Unit` and key them by B3 suffix (3 = ON, 4/5/6 = PN).
- **At most one class per kind, or none.** The filed share *counts* are per-kind
  totals (all common, all preferred), so a second class of the same kind would
  multiply that whole count twice. A company with two PN classes therefore
  resolves to **no** classes — the cap stays a named null rather than a wrong
  number (the project's null-over-a-wrong-number rule).

## Consequences

- Any on-demand ticker whose company has a clean ON(/PN) structure now gets a
  real cap and the full valuation set — KLBN11 and ITSA4 included. The
  `missing_share_count` nulls collapse to values.
- The FCA-derived classes reproduce the curated `LISTED_CLASSES` exactly for the
  nine (pinned by a test), so the nine keep their verified, offline composition
  and the fidelity gate (#44) is unaffected — the curated map still wins for them.
- A multi-PN-class company (rare — PNA/PNB, #72) still gets a null cap by design,
  not a biased one. Handling per-class counts is a separate, later problem.
- The unit *bundle size* (how many shares one unit is, #38) is still not solved;
  it is not needed here — the cap sums the underlying classes, never the unit.
  The per-share indicators (LPA/VPA) for an on-demand unit stay null.
