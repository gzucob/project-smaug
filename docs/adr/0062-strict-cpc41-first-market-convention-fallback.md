# 0062 — Strict CPC 41 first, market convention as an explicit fallback

- **Status:** Accepted
- **Date:** 2026-08-15

## Context

The security-level P/L uses CPC 41's class result and weighted-average shares.
Some issuers have a valid filing but the structured mirror cannot reconcile a
complete TTM denominator: classes can report different results, a class leaf
can be absent, or the filed result can be unusable for denominator recovery.

Leaving every such security without a per-paper valuation is useful for audit
purity but not for market reading. Replacing the strict value in place would
hide the accounting limitation and make a closing-share estimate look like a
CPC 41 result.

## Decision

- Attempt the strict CPC 41 `eps_basic` and `pe_basic` first, unchanged.
- Keep those strict fields null, with their original CPC 41 null cause, when the
  required weighted-average/class-rights evidence is unavailable.
- Calculate separate `eps_basic_market` and `pe_basic_market` values from
  attributable annualized net income divided by the CVM closing outstanding
  share count, then the B3 security price divided by that estimate.
- Use the market-convention fields only as the user-facing fallback when the
  strict field is null. The API contract and UI must label the value as an
  estimate outside the CPC 41 basis.
- Use only CVM/B3 inputs. Do not import values from external aggregators or
  infer class-specific economic rights that the filing does not establish.

## Consequences

- Bank and other dual-class tickers remain auditable: the strict result and its
  reason are preserved, while a clearly labeled market estimate is available.
- The fallback is not a class-attributable CPC 41 LPA. It allocates the
  controllers' result over closing shares, so it can differ materially when
  rights, issuance or repurchase timing differs by class.
- Diluted P/L has no equivalent fallback: potential-share terms are not
  reconstructed from closing shares and remain governed by the strict field.
