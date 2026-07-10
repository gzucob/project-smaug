# 0006 — CVM is the primary ingestion source; brapi stays as a swappable alternative

- **Status:** Accepted
- **Date:** 2026-07-06
- **Provenance:** recorded retroactively on 2026-07-10 from `docs/PLANO_FASE1.md`
  §0 (deleted; recoverable in git history). See `.claude/RULES/RULES_DOCS.md`.

## Context

Phase 1 was planned around **brapi as the single source**. Implementation
disproved the plan: **brapi's free plan does not cover the portfolio.** Only
PETR4 and VALE3 return data; every bank and insurer answers `403`
(plan-restricted). Four of the nine tickers are financials, and they are the
half of the portfolio where the accounting is hardest — exactly the half we
could not see.

CVM's open data (`dados.cvm.gov.br`) covers every listed company, for free, with
no token. It publishes annual ZIPs of the statements (BPA, BPP, DRE, DFC), and
banks and insurers appear in their own regulated formats rather than being
squeezed into a generic schema.

Abandoning brapi outright was not desirable either: the paid plan may be bought
later, and brapi remains the only source of prices.

## Decision

**CVM is the primary source of fundamentals.** `INGESTION_SOURCE` (`cvm` default
| `brapi`) selects the active one; both implement the same `RawDataSource` port
(`ingestion/domain/ports.py`), and `RawIngestion.source` tags every record, so
the two coexist in the same `raw_ingestions` collection.

brapi remains the **price source** for Phase 2 regardless of which ingestion
source is active — current quote and the dividend-adjusted series (ADR 0001).

CVM datasets are keyed by `CD_CVM`, never by the B3 ticker, so a curated
ticker → code map lives in `portfolio/domain/cvm_codes.py`, verified against the
company names in the real ITR file.

## Consequences

- **Swapping the source costs no rewrite.** The seam is a port, not a
  conditional. Buying brapi's paid plan is a config change.

- **The ticker → CVM code map is curated by hand.** It does not scale past the
  portfolio. A company registry replaces it at B3 scale (milestone M2), and ADR
  0004 later added a *second* hand-curated map (ticker → CNPJ, for the FRE), so
  there are now two.

- **pycvm is load-bearing and quirky.** Its DMPL parser crashes on the real ITR
  (`KeyError: 'Patrimônio Líquido'`), so `_sanitize_dmpl` in `cvm_source.py`
  empties the DMPL members (keeping a valid header) before parsing — we do not
  need DMPL, but the parser walks the whole file. It also ships no type stubs,
  hence the sanctioned mypy override for `cvm.*`. ADR 0004 records a second
  quirk: it cannot read the modern FRE at all.

- **Equity sits under a different code per accounting regime.** Banks file
  `2.07`, standard companies file `2.03`. The completeness report therefore
  matches equity by **name** and revenue by **code** (`3.01`). This asymmetry is
  the crude ancestor of the accounting-regime gating planned for M1.

- **CXSE3 (Caixa Seguridade) files as a holding, not as an insurer.** Its DRE
  `3.01` is "Receita de Venda", not "Atividades Seguradoras", so the sector
  report flags "Receita de seguros" as absent for it. **This is a discovery
  about the filer, not a bug in the mapping** — and it is one of the causes a
  null must be attributed to before M0 can close (#30).

- **A security fix came out of this.** `httpx` logged the request URL including
  `?token=`, leaking the brapi token into the logs of a public repository. The
  `httpx` logger is silenced below `WARNING`.
