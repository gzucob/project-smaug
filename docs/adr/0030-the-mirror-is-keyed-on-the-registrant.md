# 0030 — The raw mirror is keyed on the registrant, not on the ticker

- **Status:** Accepted
- **Date:** 2026-07-30

## Context

Phase 1 stored each mirrored filing under the B3 ticker it was collected for.
While the tool followed nine hand-picked tickers that was invisible: no two of
them belong to the same company.

Running the whole exchange makes it visible. The CVM FCA registry lists 506
trading codes over 368 companies — 109 companies list more than one code, one of
them seven. And a filing is the *company's*: Eletrobras files one DFP, which
ELET3, ELET5 and ELET6 all read. Under the ticker key, collecting the three
means storing that DFP three times.

The cost that mattered was not the disk. It was that three copies are three
chances to disagree about one fact. They are written at different moments, from
archives that CVM amends, by runs that can fail halfway — and nothing in the
system compares one ticker's mirror against another's, so a divergence would
never surface as anything but two screens quoting different net incomes for the
same company. This project has spent whole sessions on exactly that class of
bug (#78, #118), where a wrong number was indistinguishable from a right one
until something else was measured against it.

The registrant key was already in the data, unindexed and inconsistent: the
statement sources record `request.cvm_code`, the FRE-based capital sources
record `request.cnpj`.

## Decision

A mirrored document names the registrant that filed it, in a top-level
`cvm_code` field, indexed as `(cvm_code, module, fetched_at)`. **Every reader of
the CVM mirror filters on that field**, resolving ticker → `CD_CVM` through the
same registry the sector and share-class resolvers already use.

`ticker` stays on the document and stays informational: it records which code
the collection was requested under. It is also the fallback key — a document
that names no registrant is read by ticker, which is what a brapi document is
and what every CVM document was before this.

The batch iterates **companies**, not tickers: `listed_companies` groups the
registry's trading codes by `CD_CVM` and names each company by its ON share.

## Consequences

A company's classes share one mirror. PETR3 reads statements collected under
PETR4 without a second byte being stored; a correction to the account mapping
re-ingests a company once rather than once per class. Whole-exchange ingestion
of eleven closed years is 106k documents and 187 MB rather than ~27% more of
both.

The registrant becomes a second thing that must be resolved before the mirror
can be read, and resolving it wrongly is silent in the worst way: a filter that
names a registrant nobody filed under returns an empty cursor, which reads as a
company that filed nothing. That is why the filter is built in exactly one place
(`analysis/infrastructure/mirror.py`) rather than spelled out at each of the
five call sites.

Documents mirrored before this carry no registrant and are relabelled by
`smaug relink`, which reads the ticker each was collected under and stamps the
company it resolves to. It writes no payload and downloads nothing. A ticker
that resolves nowhere is reported and left on the ticker key rather than guessed.

One company can end up holding documents labelled with two different tickers —
Itaúsa's earlier years say `ITSA4`, its later ones `ITSA3`, because the batch
names a company by its ON share and the curated nine were collected under
whichever code the portfolio holds. Nothing reads that field, so nothing is
wrong; it is the visible trace of a key that used to mean more than it does.

brapi keeps the ticker key permanently. It has no notion of a registrant, so the
fallback is not a migration step to be removed later — it is the shape of the
other source.
