# 0009 — Read the CVM statement CSVs directly, without pycvm

- **Status:** Accepted
- **Date:** 2026-07-10

## Context

The CVM statement source (`cvm_source.py`) parsed each yearly ITR/DFP ZIP with
**pycvm**'s `DFPITRFile`. pycvm reads the eight statement CSVs simultaneously,
assuming every file lists companies in the same order as the head index. Two of
its behaviours actively corrupted the mirror:

- Its **DMPL parser crashes** on the real files, so we had to rewrite each ZIP
  with the DMPL members emptied (`_sanitize_dmpl`) before parsing — a workaround
  for a statement we do not even use.
- Its **parallel reader desynchronises** on a duplicated head row (e.g.
  `INTER & CO` in DFP 2021, filed twice at the same version). From that row on,
  every following filer loses its whole consolidated collection, so
  `doc.consolidated` reads `None` though the statement was filed. Our
  `_pick_collection` then fell back to the **individual** (parent-only) statement
  — a different, wrong figure stored with no signal. WEGE3 2021 was mirrored with
  total assets R$ 13.8 bi instead of the consolidated R$ 23.9 bi (#55; the FCF
  symptom is #41).

An interim guard (#56) made ingestion **fail loudly** on that desync rather than
store the wrong statement. It stopped the bleeding but did not let us ingest the
affected files at all.

pycvm's `DFPITRFile` was its only runtime use in the codebase — the FRE share
counts are already read straight from CSV (`cvm_capital.py`, ADR 0004), because
pycvm rejects the modern FRE too.

## Decision

**Read the statement CSVs directly, and drop the pycvm dependency.**

`_build_index` opens the yearly ZIP and reads each `{BPA,BPP,DRE,DFC}_{con,ind}`
member itself (the cash flow's indirect/direct members both fold to `DFC`),
keeping only the reported period's rows (`ORDEM_EXERC` = ÚLTIMO). It then reduces
to one document per `(cvm_code, reference_date)` by:

- **Highest `VERSAO` wins** — the amendment supersedes earlier filings. (pycvm,
  and our index before this, kept whichever version came last in file order.)
- **Consolidated preferred per module, individual as fallback** — read off the
  member's own name, so a dropped consolidated is impossible: each statement is
  parsed on its own, never grouped through a shared anchor that can desync.

This mirrors `cvm_capital.py`'s existing direct-CSV approach (same latin-1 /
semicolon parsing, same highest-version rule). It removes the `_sanitize_dmpl`
workaround, the `cvm.*` mypy override, and the interim guard of #56, which this
supersedes.

## Consequences

- **The desync class of bug cannot recur.** Reading each member independently has
  no cross-statement ordering assumption, so no duplicated head row can drop a
  consolidated statement. Verified: on the real DFP 2021, WEGE3 and BBDC4 now read
  consolidated, and a period pycvm handled correctly (WEGE3 2022) reproduces
  byte-for-byte — identical accounts, balance type, and scale across all four
  statements.

- **Version selection is now correct, not incidental.** Picking the highest
  `VERSAO` fixes a latent bug where a re-filed period could keep the superseded
  version depending on file order.

- **The mirror already in Mongo is still wrong until re-ingested.** This change
  fixes the *reader*, not the stored data: the append-only mirror keeps its old
  documents, and `analyze` reads the latest by `fetched_at`. #55 and #41 close
  only after re-running ingestion for the affected years and confirming WEGE3
  2021's total assets read R$ 23.9 bi (and its FCF is non-null).

- **We now own the CVM statement parsing.** That is more code to maintain against
  CVM's format, but it is ~150 lines of plain CSV reading we fully control, versus
  a dependency that shipped two correctness bugs into a faithful mirror. It is also
  the seed of a future standalone CVM reader.

- **`document_type` is now taken from which file we downloaded** (ITR vs DFP),
  not from a per-row tag. This is equivalent — the two documents ship in separate
  yearly ZIPs — and drops a field we used to read back from pycvm's document enum.
