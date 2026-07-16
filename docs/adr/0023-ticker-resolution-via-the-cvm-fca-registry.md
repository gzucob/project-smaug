# 0023 — Ticker resolution via the CVM FCA registry

- **Status:** Accepted
- **Date:** 2026-07-15

## Context

Every downstream source is keyed by CVM registrant: the statements (DFP/ITR) by
`CD_CVM`, the FRE capital and the DFP composition by `CNPJ`. The B3 trading
ticker exists in none of them. Until now the two links —
`TICKER_TO_CVM_CODE` and `TICKER_TO_CNPJ` (`portfolio/domain/cvm_codes.py`) —
were hand-curated for the nine portfolio tickers, and `require_portfolio_tickers`
actively rejected anything else before a download could start. That is the wall
M2 has to remove to ingest an arbitrary ticker (e.g. KLBN11) on demand, and
eventually the whole exchange.

The link does exist in CVM open data, in the *Formulário Cadastral* (FCA), one
yearly ZIP whose members join on CNPJ:

- `fca_cia_aberta_valor_mobiliario` carries `Codigo_Negociacao` (the B3 ticker)
  and `CNPJ_Companhia` — but **not** `CD_CVM`.
- `fca_cia_aberta_geral` carries `CNPJ_Companhia`, `Codigo_CVM`,
  `Setor_Atividade` and `Situacao_Registro_CVM`.

So `ticker → CNPJ` (securities) joined with `CNPJ → CD_CVM` (general) yields the
full identity. The B3 economic taxonomy (setor → subsetor → segmento) is **not**
here — the FCA's only sector is the CVM's single `Setor_Atividade`, and the
securities member's `Segmento` is the *listing* segment (governance level), not
the economic one. The three-level taxonomy is a separate B3 artifact, handled in
a later slice.

## Decision

Resolve a ticker to its CVM identity from the FCA archive, at the composition
root, via a `CompanyRegistry` port (`portfolio/domain/ports.py`) implemented by
`CvmCompanyRegistry` (`portfolio/infrastructure/cvm_registry.py`). It downloads
and caches `fca_cia_aberta_{year}.zip` like the other CVM sources, joins the two
members on CNPJ, and returns a frozen `CompanyIdentity`
(`ticker, cd_cvm, cnpj, denom, cvm_sector, situation`).

The curated nine keep their verified `cvm_codes.py` keys and never trigger an FCA
download; any other requested ticker is resolved on demand. A ticker that
resolves nowhere raises `UnknownTickerError` — the same clean exit the curated
guard gave, now meaning "CVM does not list this" rather than "not one of the
nine". `require_portfolio_tickers` is retired from the CLI path.

The five-value `Sector` an on-demand ticker needs (for display and the
`expected_regime` fallback) is folded from the CVM `Setor_Atividade`
(`sector_from_cvm`). It is deliberately coarse: indicator applicability rides on
the **filed regime** read off the statement itself (ADR 0020), not on this
sector.

## Consequences

- Any company listed in the FCA of the configured year can be ingested and
  analysed on demand, with no code change — the wall is gone. The registry index
  is exactly what a batch run over the whole exchange will iterate (the next M2
  slice).
- `cvm_codes.py` is kept as the nine's verified, offline keys **and** as a test
  oracle: `test_company_registry` proves the registry reproduces those exact
  keys from a synthetic FCA archive.
- Resolution is scoped to one FCA year (`cvm_year`). A ticker delisted before
  that year, or listed after it, will not resolve — acceptable for on-demand
  work now; broadening across years is a follow-up.
- The FCA does **not** carry the B3 economic taxonomy, so this ADR does not
  deliver it; it carries only the CVM's single activity label. The three-level
  taxonomy (and replacing the `Sector` enum) is a separate decision.
- An on-demand ticker whose share classes are not curated in `share_classes.py`
  gets a null market cap (and the multiples that divide by it), reported by
  `smaug doctor` as `missing_share_count` — a known, named gap, not a silent
  one. The class composition needed to close it is itself in the FCA
  (`Composicao_BDR_Unit`), which is a follow-up.
