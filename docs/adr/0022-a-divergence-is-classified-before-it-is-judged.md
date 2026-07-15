# 0022 — Every indicator has a specified basis; a platform divergence is classified before it is judged

- **Status:** Accepted
- **Date:** 2026-07-15

## Context

Mathematics is exact. Given the same formula and the same numbers, everyone gets the
same result. So when our indicator differs from a reference platform's, exactly one of
two things is true: the **formula** differs, or the **inputs** differ — a different
statement, period, basis, or share base. A divergence is therefore an open question
about *our own specification*, never a verdict about the other party.

The M1 fidelity gate (#44) got this backwards. Its fixture wrote sentences like *"the
platform is not consistent with itself"* into `except` blocks and asserted them as fact —
several of them against a **single** source, without ever opening the primary material.
Two facts show why that method was wrong:

- **The reference platforms disagree with each other.** On a bank's current ratio, AUVP
  publishes 9.47 and Investidor10 1.02 — a 10× gap. On BBSE3's book value per share, three
  platforms land three ways (Dados de Mercado 5.35, ours 5.52, AUVP/Investidor10 6.51).
  "Matching the platform" was never a coherent goal; picking one silently made its basis ours.
- **Our own basis was never written down in one place.** Each of ADRs 0002, 0003, 0015,
  0017, 0018, 0019 and 0021 fixes one axis of one indicator, but nothing collected them, so
  a divergence had nothing exact to be measured against.

## Decision

**Method first, code second.** Two decisions.

### 1. Every published indicator's inputs are specified along four axes

The **statement**, the **period**, the **basis**, and the **share base**. The cross-cutting
conventions, each already decided in its own ADR, are the specification:

| Axis | Convention | Fixed by |
|---|---|---|
| Statement | Consolidated, latest amendment, reported period; **bank income from the parent filing**, bank balance sheet consolidated | ADR 0019, 0015 |
| Controllers vs minority | The controlling shareholders' slice of every result/equity line | ADR 0019 |
| Period (live) | Trailing 12 months, flows annualized by period length | calculator |
| Period (history) | The closed-year DFP; annualization a no-op | calculator |
| Result basis | **Accounting** result (as filed), not the company's *adjusted/recurring* headline | ADR 0019 |
| Equity basis | **Closing** balance, not a two-year average | ADR 0003 |
| Dividends | Cash **paid** in the period (DFC), not **declared** for the exercise | mapper |
| Share base | **Outstanding** = issued − treasury; equity read **net** of treasury (2.03 total) | ADR 0017 |
| Price basis | **Nominal** close, not the dividend-adjusted series | ADR 0018 |

The contested indicators inherit these: a bank margin is the **parent** spread over the
parent revenue (ADR 0019); an insurer margin is **degenerate** because a holding earns
through equity-method income and files a revenue line of ≈ 0 (ADR 0010, 0015).

### 2. A divergence is classified into one of four buckets before it is judged

And the classification is drawn from the **filings and the companies' own performance
reports** — never by reverse-engineering a second platform:

- **(a) a different statement** — parent vs consolidated;
- **(b) a different period or basis** — TTM vs closed year; dividends paid vs declared;
  accounting vs adjusted result; closing vs average equity;
- **(c) a different share base** — issued vs outstanding; equity gross vs net of treasury;
  split-adjusted vs as filed;
- **(d) a genuine defect** — and only a (d), demonstrated arithmetically, changes the
  calculator or the mapping.

No `except` reason may say "the platform is wrong" without an arithmetic demonstration; a
divergence whose cause is unknown says *unknown*, loudly. A tolerance is never widened to
hide a divergence, and platform parity is never a goal in itself.

## Consequences

- **Our numbers will not match any single platform, and that is correct.** The platforms
  answer different questions and disagree among themselves; the fixture now records the
  bucket behind each accepted divergence, so the disagreement stays legible instead of
  being flattened into "we match Dados de Mercado."
- **Triage costs primary-source discipline.** A bank's or a holding's own results report has
  to be read before a divergence is judged. That discipline is what separates a real profit
  collapse (BBAS3 closed 2025 at R$20.7 bn on the agribusiness crisis — bucket none, not a
  bug) from a period defect, and an equity gap (BBSE3's book value: the platforms show the
  current quarter, where BBSE3 cancelled its treasury and equity genuinely rose to R$12.64 bn
  — a period difference our own TTM view reproduces exactly) from a missing equity line. Both
  were nearly misjudged; only reading the filed balance sheets settled them.
- **The specification is the input a future indicator change needs.** The three PRs it took
  to get a bank's income statement right (#92 → the bank lines → #99) would likely have been
  one, had the basis been written down first.
- **Bucket (d) is the only path from a divergence to a code change.** The audit that produced
  this ADR classified every financial-sector divergence as (a), (b) or (c) and found no (d).
  A future audit that finds one opens an issue with an arithmetic demonstration — it does not
  widen a tolerance.
- This rules out the failure mode #44 fell into: an `except` reason that is a **claim about
  the platform** rather than a **hypothesis about our basis, carrying its bucket and evidence**.
</content>
