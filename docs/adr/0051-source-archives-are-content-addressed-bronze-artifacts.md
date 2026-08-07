# 0051 — Source archives are content-addressed Bronze artifacts

- **Status:** Accepted
- **Date:** 2026-08-06

## Context

The CVM readers parsed yearly ZIPs from an operational cache. A cache filename
identified a publication slot, not the bytes read from it: CVM can republish the
same filename, while cache eviction can remove the only local copy. Consequently,
a mirrored document could name its parser and ingestion run without proving which
source bytes produced it, and a parser correction still depended on the network.

An archive's content identity and an HTTP observation are different facts. The
same bytes can be observed more than once or through more than one URL, while a
single stable URL can publish different bytes over time.

## Decision

Every archive used by ingestion is preserved before parsing in a Bronze source
layer. Its identity is `sha256:<digest>` and its recorded size is the exact byte
length hashed. The initial backend is local and publishes validated ZIPs into an
immutable content-addressed namespace only after acquisition completes in a
separate staging area.

HTTP observations record the source URL, UTC download time, `ETag`, and
`Last-Modified` when supplied. Conditional requests may avoid transferring
unchanged content, but HTTP headers never replace the content digest. Identical
bytes reuse one artifact; changed bytes under the same URL create another and do
not remove the earlier artifact.

Ingestion runs and archive-derived raw mirror documents reference the artifact
identity. Resume decisions for archive modules use that identity rather than the
publication filename. Readers can open an existing artifact by identity and
parse its local path without a network request.

Artifacts have no automatic expiration or garbage collection. Any future
retention mechanism must be explicit and reference-aware. The storage boundary is
a port so a later object-store adapter can materialize the same artifact contract
without changing parsers.

## Consequences

The exact source bytes behind the MongoDB structural mirror remain replayable and
auditable after cache cleanup or source republication. A republication is
distinguishable from a parser change, and interrupted or corrupt downloads never
enter the immutable namespace.

Ingestion now performs a source revalidation before deciding that an archive
module is already mirrored. Local storage grows monotonically until an explicit,
safe retention policy exists. The local backend and its metadata need operational
backup or a persistent volume in deployments; content addressing prevents
accidental overwrite, not disk loss.

The Bronze layer stores source bytes, MongoDB remains the Silver faithful
structural mirror, and PostgreSQL remains the Gold derived analysis. This decision
does not deduplicate parsed filings or define semantic batch validation.
