# 0056 — B3 company names follow the CVM registrant

- **Status:** Accepted
- **Date:** 2026-08-11

## Context

`GetListedSupplementCompany` is addressed by a four-character trading root.
That root can be renamed, retired or reused while the CVM registrant remains the
same legal issuer. An FCA archive can therefore name a valid historical code
whose old root no longer answers B3's current supplement. Both
`CAPITAL_EVENT_B3` and `CASH_DIVIDEND_B3` depended on that one lookup, so the
same identity gap could quarantine both modules before a mirror rebuild.

`GetDetail` answers by `CD_CVM`, returns the current `issuingCompany` and
`tradingName`, and repeats the registrant code. The paginated cash endpoint,
however, returns no company identifier at all. Guessing or truncating a name can
therefore return a complete and plausible history belonging to another issuer.

## Decision

- The composition root's CVM registrant code is the company identity used to
  resolve B3 listed-company endpoints.
- The requested trading root is tried first. When it is absent or belongs to a
  different registrant, `GetDetail` is queried by `CD_CVM`; its current root is
  then used to fetch a new supplement. The detail and final supplement must both
  repeat the expected registrant code.
- `CAPITAL_EVENT_B3` reads stock events from that verified current supplement.
  `CASH_DIVIDEND_B3` uses its verified `tradingName` in the paginated endpoint.
  The exact slash-to-dot corporate-form retry remains allowed, but no prefix,
  truncation or fuzzy name search is introduced.
- The persisted request and payload name the current B3 root and the actual
  trading name that produced the rows. The original ticker remains on the raw
  ingestion record, and `cvm_code` remains the mirror key.
- An absent link, malformed response or registrant mismatch is quarantined.
  Only a completed, registrant-verified cash query with zero rows establishes an
  economic zero. The coverage-validation rule advances to version 2.

This supersedes ADR 0039 only where it treated the old trading root's supplement
as the sole source of a cash-table name. Its cash-factor and pagination decisions
remain unchanged.

## Consequences

- Renamed and retired trading roots can recover their B3 histories without
  attaching a similarly named issuer.
- A root reused by another registrant cannot silently cross company boundaries.
- The fallback costs two additional public B3 calls only when the original root
  is absent or fails the registrant check; normal current roots keep the direct
  path.
- Companies absent from both B3 identity endpoints remain quarantined and read
  as named nulls rather than as zero-event histories.
- Both B3 modules share the same resolution rule before their raw identities are
  rebuilt, avoiding a second destructive replay for the sibling module.
