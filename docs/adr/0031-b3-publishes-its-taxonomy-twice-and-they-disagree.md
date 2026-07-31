# 0031 — B3 publishes its taxonomy twice, and the two disagree

- **Status:** Accepted
- **Date:** 2026-07-31

## Context

ADR 0024 put the B3 three-level classification in a committed snapshot, hand-typed
for the fifteen tickers then analysed, with the CVM's single `Setor_Atividade` as
the fallback for everything else. Analysing the whole exchange turned that
fallback from an edge case into the norm: 491 of 506 tickers took it, and it
answers with 56 cadastral activity strings — `Emp. Adm. Part. - Sem Setor
Principal` — where B3 has eleven economic sectors.

Regenerating the snapshot meant choosing a source, and B3 offers four:

* `bvmf.bmfbovespa.com.br/InstDados/.../ClassifSetorial.zip` — the legacy
  download. Still returns 200; its single member is named
  *Setorial B3 15-09-2022*. Frozen for four years. Public projects consume it.
* The current file on `www.b3.com.br`, behind a JavaScript-rendered page at a
  content-hashed URL — not fetchable without a browser.
* `GetDownloadIndustryClassification` — an `.xlsx` with the three levels in
  their own columns, current, one request, keyed by the four-letter **trading
  root** (`PETR`, `AXIA`).
* `GetDetail` — per company, keyed by **`CD_CVM`**, serialising the three levels
  into one string.

Two facts settled the design. First, **the trading root is B3's current name for
the company, and our universe comes from a CVM archive of a given year**: 30 of
506 tickers — ELET3/5/6, BRFS3, CCRO3, BRML3 — cannot be found on the
spreadsheet at all, because B3 lists them under the name they took after the
archive was published. `CD_CVM` never changes and reaches them.

Second, **the two surfaces do not word the taxonomy identically**. 103 of 109
labels agree, and the rest are B3 disagreeing with itself: the spreadsheet says
`Comércio Varejista`, `Produtos de Cuidado Pessoal`, `Linhas Aéreas de
Passageiros` where the per-company endpoint still says `Comércio`,
`Produtos de Uso Pessoal`, `Transporte Aéreo`. The endpoint additionally writes
commas as periods (`Petróleo. Gás e Biocombustíveis`).

It also emerged that the fifteen hand-typed entries had been verified against a
*third* surface — B3's public web tool — which renders labels the published
spreadsheet abbreviates: `Serviços Médico-Hospitalares, Análises e Diagnósticos`
against `Serv.Méd.Hospit.,Análises e Diagnósticos`.

## Decision

**The spreadsheet is the authority on what a label says; the per-company
endpoint is the authority on which companies exist.** The refresh reads the
spreadsheet first and joins by trading root, then asks `GetDetail` per `CD_CVM`
only for the companies the root join could not reach — the same primary/fallback
shape ADR 0013 gives the price providers, for the same reason: neither source is
wrong, they are incomplete in different directions.

A label from the fallback is corrected through a table verified against the
spreadsheet, covering the comma defect and B3's one rename so far. A fallback
label **outside the spreadsheet's vocabulary is reported, not corrected** — the
spreadsheet defines what a label may be, so anything else is a change in the
source rather than something to guess at.

The snapshot is a generated JSON file, and `smaug taxonomy` reports drift
(exiting non-zero) or rewrites it with `--write`.

## Consequences

446 of 506 tickers carry the three levels, against 15 before, and the distinct
`setor` count drops from 56 cadastral strings to B3's 11. The 41 companies
neither source classifies are in judicial recovery, liquidation or bankruptcy —
B3 drops them from the taxonomy while CVM still registers them, and the CVM
fallback is exactly what that is for.

Some labels now read oddly, and it is B3's text rather than ours:
`Motores , Compressores e Outros` keeps a space before its comma, and
`Serv.Méd.Hospit.,Análises e Diagnósticos` is abbreviated. Preferring the web
tool's rendering would read better and would mean maintaining, by hand, a
transcription of a surface B3 does not publish as data.

The correction table is a standing liability: it is the one place where our text
differs from what a source returned, and every entry needs a human to have
checked it. It is kept small by construction — the vocabulary check refuses to
extend it silently — but a session that adds an entry without opening the
spreadsheet has broken the property this rests on.

The refresh costs one request plus one per unreachable company, about thirty
today. That number grows with the age of the ingested FCA archive relative to
B3's current names, which is a reasonable thing for it to track.
