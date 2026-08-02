# 0036 — A declared action anchors on either end of the move it states

- **Status:** Accepted
- **Date:** 2026-08-02

## Context

ADR 0027 restates share counts by the ratio CVM *declares*, and matches a
declaration to a filed step by its **`total_before`**: the count the action
started from has to be a count some FRE filed. Where it is not, the action
anchors on nothing and the chain falls back to the inference — which fails too
whenever an issuance rode along with the action, because the filed ratio is then
dirty and a dirty ratio is deliberately restated by nothing.

TOTS3 is the case the rule cannot reach:

```
FRE 2018   165,637,727
FRE 2019   577,913,181            filed ratio 3.489x -> dirty -> factor 1
CVM 2020-04-27  Desdobramento     192,637,727 -> 577,913,181   (x3)
```

No FRE ever filed 192,637,727 — 27 million shares were issued between the 2018
reading and the split — so the `before` matches nothing. The `after` matches the
2019 filing to the share. Both CVM and B3 state the same ×3 and the restatement
used neither, leaving TOTS3 **200% out** against the vendor's back-adjusted
series for 2015–2019.

## Decision

**A declared chain may be read from either end.** Forwards it starts at the
earlier filed count and matches on `total_before`, as before; where nothing
starts there it is read backwards from the later filed count, matching on
`total_after` and walking back through `total_before`.

Forwards is tried first, so an action that starts where the filing starts is
still the one that describes the move. Everything else is unchanged: the chain
stops where nothing further matches and keeps what it has (which is what leaves
an issuance out), each approval is spent once, and a standstill year declares
nothing.

## Consequences

Measured over the whole exchange, before and after, on the 5,544 stored views:

```
identical                     266,770
<= 0.05% (source rounding)        612
> 1%                              602      16 tickers
value -> null                       0
null -> value                       0
```

Against the vendor's back-adjusted series — the independent witness, since it
embodies the true factor — those moved cells go **68 better, 4 worse, 16
unchanged**. TOTS3 falls from 200% to 0.3%, CSAN3 from 300% to 0.00%, NUTR3 from
9,283% to 6%.

**The four that got worse are explained, and none of them contradicts the
change.** B3's own tape (ADR 0035) marks the base change each one adds:

- **CASH3 2020/2021** — the tape marks the declared ×6 on 2021-09-10. What the
  chain is still missing is a *different* action: a 1:10 grupamento on
  2023-06-01, which the filed counts refuse because 865,180,443 → 86,957,953 is
  not clean. Restating by one of two missing factors reads as a regression
  against a series that has both.
- **RCSL3 2018** — the tape marks the declared ×0.5 on 2022-06-27, and three
  further base changes (2023, 2024, 2026) the chain does not have. 2018 has no
  filed count at all.
- **NUTR3 2015** — the same ×100 that takes 2016–2023 from ~9,000% error to
  single digits. NUTR3 traded a handful of sessions in 2015, and a vendor
  average over three prints is not a witness.

That residual is filed as an issue: it is a **ratio the counts refuse on both
ends**, which is the next question after this one and not this one.

**Anchoring on two ends doubles the ways an action can be claimed.** The guards
that already existed carry the weight: an approval is consumed once, a
standstill year declares nothing, and the match tolerance is 1e-9 on a quantity
both sources read from the same archive. What this does not add is a *date* —
the timeline still takes its dates from the feed and the tape (ADRs 0034/0035).
