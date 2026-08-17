# ADR-0065: Preserve B3 price-null and FCA identity provenance

- **Status:** Accepted
- **Date:** 2026-08-17

## Context

The company market cap is assembled from the prices of every listed share
class. A missing sibling price used to become the generic `MISSING_PRICE`,
discarding a stronger source diagnosis such as a successor code that B3 cannot
name. Archive failures were also indistinguishable from a year in which a known
code simply had no session. Finally, two same-priority FCA rows could map one
ticker to different CNPJs and the CSV order would decide which identity won.

## Decision

Keep price-null causes on each class while the cap is assembled and return the
selected class cause as the cap cause. Use separate causes for a plain missing
session, an unknown symbol, an unavailable source, malformed B3 content, and a
timeout. The calculation remains B3-only; no vendor value is introduced.

When FCA rows tie on trading status and document version but contain different
CNPJs, retain the candidate set on the resolved identity and exclude the ticker
from fundamental analysis and the listed universe. A higher-priority FCA row may
resolve the ambiguity; CSV order alone may not.

## Consequences

- `smaug doctor` and persisted indicator `null_reasons` can distinguish a
  transient market gap from a structural code or source failure.
- A company cap remains all-or-nothing, while the missing class and its reason
  remain available to diagnostics.
- Ambiguous FCA mappings fail closed instead of contaminating filings, share
  counts, price succession, or market capitalization.
