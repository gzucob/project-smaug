# 0042 — The price follows the security, not the trading code

- **Status:** Accepted
- **Date:** 2026-08-03

## Context

B3 files each year's quotes under the code that **traded that year**. A B3 code
is not an identity: a company renames it and nothing else changes (EMBR3 → EMBJ3,
November 2025), or renames it on the back of a merger it survived (ARZZ3 →
AZZA3). Read under today's code alone, everything the company traded before the
change belongs to nobody.

Counting the *missing* years understated it. The loud half is a year with no
price at all; the silent half is the **year of the change**, served from whatever
fraction of it fell under the new code, with no null and no warning because a
price does exist. Measured against the full year the market actually traded:
AMER3 2021 came out 31.7% low (114 of 247 sessions), EMBJ3 2025 17.4% high (39 of
250), AZZA3 2024 14.7% low (104 of 251). Every price-derived indicator of that
exercise carried the same error and `smaug doctor` reported the cell green.

Three candidate identities were available, and two of them are wrong:

- **The root** (`AZZA` vs `ARZZ`) — B3 hands a retired root to whoever asks next,
  so matching on it can return a valid history belonging to another company. The
  same trap as #190.
- **The company name** — a rename usually changes it too (`AREZZO CO` →
  `AZZAS 2154`). Sweeping COTAHIST's `NOMRES` finds 48 apparent root changes,
  nearly all of them subscription and receipt codes, and misses every real one.
- **The registrant** (CNPJ) — right in almost every case and wrong in the one
  that matters most. Rumo's registrant *is* ALL's old CNPJ, so "same company"
  would prepend a series quoted on another share entirely.

## Decision

A year is priced under **every code the security has traded as** — the same
registrant's same share class — and the codes are joined only where the evidence
says they are one series. The FCA supplies the candidates (which codes a CNPJ has
filed for a class); COTAHIST says when each of them traded; and three conditions,
all required, decide a join:

1. **Same class**, matched on the exact suffix digit — 5 and 6 are PNA and PNB,
   and a unit is a different share base again.
2. **Disjoint in time.** A candidate still trading alongside the served code is a
   second listing, not an earlier self (`SULA11`, `TAEE11`).
3. **The price crosses the seam.** Over 25 successions the price carries over on
   the very next session, between ×0.911 and ×1.061. The two that do not are
   precisely the two that must never be joined: `ALLL3` → `RUMO3` (×0.343, a
   share exchange) and `LLIS3` → `VSTE3` (×7.474, a grupamento executed with the
   rename). Bounded by the FCA's `Data_Inicio_Listagem`, which dates Rumo's
   security from the day the combination closed and says the same thing
   independently.

A join is **not a corporate action** — no ratio, no ex date, nothing to restate
(ADRs 0033–0038). It decides which sessions exist, upstream of the restatement,
which then applies to the recovered sessions unchanged because it is dated
session by session. For that to hold, the base-change reader walks the joined
tape too: an action filed under the earlier code has to stay dated, or the
sessions it precedes would be joined and never restated. TRPL4's 2019 action is
the witness — invisible to ISAE4's own tape, dated on the joined one.

**A year the named codes cannot cover is a null with a cause, never a partial
average.** `PRICE_SYMBOL_NOT_FOUND` already meant "the series does not carry this
code" (#64); it now also carries "the security traded that year under a code we
cannot name". A code with no session anywhere keeps its plain missing-price null
— BAUH3 has been listed since 1995 and has never traded, which is a fact about
the market (#164), not a code we failed to name.

## Consequences

Twenty-two codes read one continuous series where they read a fragment before,
and the fragment years stop being published as if they were whole. AMER3 2021
goes from R$37.50 to R$54.90, AZZA3 gains 2015–2023, BHIA3 joins three codes.
The overwhelming majority — every code that has only ever been itself — is
delegated untouched and is byte-for-byte what it was.

It costs coverage where the evidence stops, deliberately. A rename older than
2018 is not recoverable at all: the FCA's `Codigo_Negociacao` column is empty in
every earlier year, and CVM publishes one version per year, so `KROT3` is named
nowhere and Cogna's union of codes is `('COGN3',)` (#198). Those years now
publish a named null instead of an average of the sessions that happened to be
there — COGN3 2019 loses a number that was 1.7% off, and gains the statement that
we cannot price it. A seam carrying an action loses its year the same way: VSTE3
2023 was a valid average of the sessions after the grupamento and is now null,
because half that year is quoted on a base nothing downstream would restate.

Recovering either is possible and is left open: the seam is a **date**, and the
size of an action already comes from the filed counts (ADRs 0036/0038), so a
discontinuous seam could be explained rather than refused. That is a change to
the restatement chain, not to this decision, and it is #197.

The two-year lookback the debut test needs pulls two extra COTAHIST archives at
the start of a run's window, once, and the securities history pulls one small FCA
ZIP per year from 2018. Both are cached.
