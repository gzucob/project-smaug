# 0041 — The vendor chain is removed, and with it the last credential

- **Status:** Accepted (supersedes [0006](0006-cvm-as-the-primary-ingestion-source.md);
  retires the chain of [0013](0013-yahoo-primary-brapi-fallback.md), itself already
  superseded by [0032](0032-b3-publishes-the-price-series-itself.md))
- **Date:** 2026-08-02

## Context

ADR 0040 made B3's own series the default price source. That left brapi and
Yahoo wired but unreached: a `PRICE_SOURCE=vendors` branch, a
`INGESTION_SOURCE=brapi` branch, a token, a pacing knob, and four price modules
nothing called.

Dormant code is not free. It reads as a live alternative to whoever finds it
first; it keeps a credential in the configuration of a public repository; it
holds a second vocabulary for the same concepts (`quarters` against `accounts`,
brapi field names against filed account codes); and it makes every future change
to a port ask "and what does the vendor do here?" about a path nobody runs.

The measured facts behind removing rather than keeping it are in ADR 0040: the
vendor mixes the price base with the count base, misses codes B3 lists, and
costs ~6,000 sequential HTTP calls a run against 3 minutes of cached archive.
The mirror settles the ingestion side on its own — **169,590 documents, every
one of them `source: "cvm"`**. brapi never filed a document that survived.

## Decision

brapi and Yahoo are deleted: the client, the price providers, the fallback and
composite chains, the vendor symbol map, the token, the pacing knob, the
`PRICE_SOURCE`/`INGESTION_SOURCE` selectors, and the tests and fixtures that
existed only for them.

Two things that were *named* for brapi are kept, and renamed instead:

- The error family. `BrapiError` and its subclasses were the whole
  application's source-failure vocabulary — raised by CVM's archives and B3's
  endpoints far more than by brapi. They become `SourceError`,
  `SourceNotFoundError`, and so on.
- `RawIngestion.source`. It records which source filed a mirrored document,
  which is provenance and stays true of the 169,590 already stored.

There is now no credential anywhere in the configuration. Every source is
public and unauthenticated: CVM's archives, B3's quote files, B3's listed
endpoints.

## Consequences

One source of prices and one source of filings, so a number in the analysis is
traceable to a published file and nothing silently answers in its place. A
missing price is now unambiguous — the exchange has no session for that code and
year — where a chain could hide the same absence behind a second source on
another basis.

It removes the escape hatch. If B3's endpoints change shape or a code's history
is filed under a name we do not follow (#193), there is no vendor to fall back
to and the gap shows as a null with a named cause. That is the intended
trade: an absence that is visible beats a number of unknown provenance.

It also ends the comparison against the vendors as a routine practice. The
exchange and CVM are the sources of truth; a platform was a pointer, never an
approval criterion, and the scaffolding comes down with the chain it was built
against. A future contracted source, if one is ever needed, arrives as a new
decision and not as the revival of this one.
