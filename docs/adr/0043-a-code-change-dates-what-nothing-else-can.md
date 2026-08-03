# 0043 — A code change dates the action nothing else can date

- **Status:** Accepted
- **Date:** 2026-08-03

## Context

ADR 0042 joins a security's trading codes where the price crosses the seam
between them, and refuses the join where it does not. One of the two refusals it
records is right forever — `ALLL3` → `RUMO3` is a share exchange, and no ratio
restates a claim that was swapped. The other was a placeholder: `LLIS3` → `VSTE3`
moves ×7.474 because Le Lis Blanc executed an **8:1 grupamento on the day it
renamed**, and the same share on a new base is exactly what a restatement is for.

Refusing cost the whole security: 2021 and 2022 had no price at all, and 2023 was
a structural null.

Measured, every source that could have dated that grupamento is silent:

- **B3's tape** restarts a new code's rights state from scratch. `VSTE3`'s first
  session carries a clean `ESPECI` and a `DISMES` of 100, so the mechanism of
  ADR 0035 — the series dating its own actions — has nothing to read.
- **B3's event feed** answers `GetListedSupplementCompany` for `VSTE3` with no
  corporate action at all, and does not resolve the retired `LLIS` root.
- **The filed counts** never make the move. Shares were issued on *both* sides of
  the grupamento, so the FRE files 68.9 M for 2021 and 113.4 M for 2022 while the
  action ran 848.6 M → 106.1 M. Neither end anchors on a filing, the gap reads as
  a dirty ×1.6475, and ADR 0027 restates a dirty ratio by nothing on purpose.

What does exist is a **declaration**: CVM files the action outright, 848,591,865
→ 106,073,983, approved 2022-12-14. The ratio was never in doubt. Only the
question of whether that declaration belongs to this gap, and of when it took
effect, had no answer.

## Decision

**The seam is a base change.** Where a security's price does not carry across a
change of trading code, that session is published as a `BaseChange` like any the
tape marks — the last close under the old code over the first under the new one —
and flagged as a seam, because it is the one kind the tape does not mark itself.
A seam whose price *does* carry over publishes nothing: a rename moves no share,
and a candidate of ratio ~1 could only be mispaired with some other action.

**A declaration the counts strand is restored when a seam witnessed it.** Where a
filing gap is left unexplained by every rule before it, a declared action whose
approval falls in that gap and whose size matches a seam inside it — one seam,
one declaration, unambiguous both ways — contributes its ratio. The ratio applied
is the **declared** one (1/8), never the market's reading (0.1338): the tape reads
a size only to ±25% (ADR 0035). The residual, here a ×13.2 issuance, keeps its
factor of 1 like every other issuance.

**The witness must be a seam and nothing else.** Every other base change the tape
publishes is already reachable through the counts — that is what `_witnessed_ratio`
does, anchored on them — so accepting one here re-applies what the counts already
carried. This is measured, not feared: dating a stranded declaration on its
approval alone moved **60 of 368 registrants** and put Alpargatas' 1.25 and
Bradesco's 1.1 on top of themselves; allowing any base change as the witness still
moved 37. Restricted to seams, and with a step of the same size already standing
disqualifying the declaration, it moves **1 of 368** — the one it was written for.

**The price then joins a seam the restatement has dated.** The chain of codes
splits in two: the *candidates* (same registrant, same class, disjoint, after the
listing floor), which the tape reader walks so that a seam can be dated at all,
and the *joined* chain, which the price averages — a candidate joins when the
price crosses its seam or when a dated step sits on it. Keeping the two apart is
what keeps this from being a circle: the tape needs the wider chain, the average
needs the narrower one, and the restatement sits between them.

## Consequences

Veste is whole: 2021 and 2022 gain a price where they had none (R$27.22 and
R$12.92, LLIS3's own sessions restated by the 8), and 2023 becomes one year
rather than the 220 sessions after the event — R$15.2919 against the R$15.4530 it
published before, the difference being January and February restated and
session-weighted in (ADR 0033).

Nothing else moves. One registrant of 368 changes its restatement timeline, and
the price of every other joined code — AZZA3, AMER3, ISAE4, BHIA3 — is unchanged
to the centavo, as is every code that never renamed.

The refusals that remain are the ones that should. A share exchange is still
refused, by the listing floor rather than by the seam. `COGN3` still publishes a
named null, because the FCA never names `KROT3` and there is no seam to date
anything with (#198).

It costs a new failure mode, bounded: a *rename* that coincides with a large,
declared, but genuinely unrelated count move could now be joined on a
coincidence. Both witnesses would have to agree in size within ±25% while the
counts strand the declaration entirely, and the exchange today offers exactly one
seam of any kind that is not crossed by the price. The guard against it is the
same as everywhere else here — two independent records saying one thing — and the
cost of being wrong is a price series on a mis-scaled base, which is the failure
ADR 0042 refuses seams to avoid.
