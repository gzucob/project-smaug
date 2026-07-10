# 0001 — Closed-year multiples reprice the market cap onto the dividend-adjusted average

- **Status:** Accepted
- **Date:** 2026-07-08
- **Provenance:** recorded retroactively on 2026-07-10 from F7 of
  `docs/FINDINGS_INDICATORS.md` (deleted; recoverable in git history). See `.claude/RULES/RULES_DOCS.md`.

## Context

`analyze` emits two views per ticker, tagged by a `view` discriminator: the live
`ttm_live` and one `closed_year` row per ingested DFP. The two need different
prices, and a closed year needs a price that no longer exists — the year is over.

Two questions had to be answered for the closed-year view.

**Which price?** A multiple for, say, fiscal year 2024 cannot use today's quote:
it would compare 2024 earnings against a 2026 price. It needs a price
representative of the year itself. It also cannot use the *nominal* quote series,
because the reference platforms (AUVP Analítica, Investidor10) build their
historical multiples on a **dividend-adjusted** series — a quote series where
each past price is discounted by the dividends paid since, so that a payout does
not read as a price drop. Measured on PETR4's fiscal year 2024, the two bases
differ materially: the dividend-adjusted average was R$ 30.48 against a nominal
average of R$ 38.20. Using the nominal average put every historical multiple
roughly a quarter above the platforms', for a purely mechanical reason.

**Which earnings?** An earlier implementation (PR #8) computed the historical
multiple from the latest ITR quarter, annualized ×4. That is a proxy for a
closed year, and a poor one: it projects one quarter's seasonality across twelve
months, when the true twelve-month figure is already on file in that year's DFP.

## Decision

For a `closed_year` view:

- **Price basis** is the year's **dividend-adjusted average price**. The market
  cap is repriced onto it, rather than recomputed from a share count:

  ```
  cap_for_year = current_cap × adjusted_avg_price / current_price
  ```

  Both the adjusted average and the nominal average are persisted; the nominal
  one is stored for reference and is not what the multiples divide by.

- **Earnings and equity basis** is the **annual DFP** figure for that fiscal
  year — a true twelve-month number, controllers' share, never annualized.
  Quarterly annualization is confined to the `ttm_live` view.

The live view is unaffected: it prices on the current nominal quote.

## Consequences

- Historical multiples (P/L, P/VP) line up with the platforms' basis, because
  both the numerator's price series and the denominator's period now match how
  the platforms build them.

- Replacing the annualized quarter with the DFP moves the multiples. On PETR4's
  fiscal year 2024 the residual gap against AUVP after the change was about 4%,
  and it is attributable to the earnings basis: true annual earnings attributed
  to controllers, against the old annualized-quarter proxy. **This is a
  deliberate improvement, not a regression** — the DFP is the faithful basis for
  a closed year, and a remaining divergence from a platform is now a question
  about *that platform's* basis, not about ours.

- Repricing the cap rather than rebuilding it from a share count means the
  multiples never touch `shares`. They are therefore immune to share-count error
  — the property that later contained the damage when brapi's company-wide market
  cap turned out to be unusable as a share-count source (ADR 0004). Only the
  absolute per-share figures (LPA, VPA) were ever exposed to it.

- The repricing is anchored on `current_price`. A closed-year row is only as good
  as the current quote it was computed from, and the row is not reproducible from
  the database alone — it depends on the quote at the moment `analyze` ran.
  Re-running `analyze` on a different day yields a slightly different historical
  cap for the same closed year.

- **Closed-year dividend yield divides that year's dividends by the repriced
  cap.** For a year with an extraordinary payout the resulting yield is very
  large — on PETR4's fiscal year 2024, near 25%. That is arithmetically what
  happened that year, not a defect, but it means the closed-year DY carries a
  different meaning from the live DY: it is a historical fact about a payout, not
  a forward-looking expectation.
