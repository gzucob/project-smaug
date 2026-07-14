# 0020 — Applicability is keyed on the regime the company files, not the one its sector predicts

- **Status:** Accepted (refines [0010](0010-financial-indicator-applicability-per-regime.md))
- **Date:** 2026-07-14

## Context

ADR 0010 decided that an indicator's applicability belongs to the **accounting regime**,
not to the economic sector, and named per regime what a filer genuinely cannot support
(a bank has no net debt; deposits are funding, not borrowing). ADR 0015 then made the
*mapper* read each filer's own chart of accounts by the regime it actually files under.

The calculator never followed. It asked `expected_regime(sector)` — the regime a ticker's
**sector predicts** — and papered over the difference with a `mismatch` flag that turned
the resulting null into `UNEXPECTED_REGIME`.

CXSE3 is what that costs. It is an insurer by sector and files as a corporate holding
(ADR 0006), so it was handed the *insurer's* inapplicable set: gross margin, EBIT margin
and EBITDA margin were suppressed for a filer whose chart of accounts supports all three.
Eighteen cells in `smaug doctor` read `unexpected_regime` — a null caused by our model of
the company rather than by the company's filing.

M1's gate says it plainly: *todo indicador inaplicável é inaplicável por regime declarado,
não por exceção codificada.*

## Decision

Applicability keys on `filed_regime` — the regime read off the filing itself. The sector's
expectation survives only as the fallback for a filing whose regime could not be detected
at all, where there is nothing better to ask.

`mismatch` and the `UNEXPECTED_REGIME` null reason are retired: with applicability read
off the filing, a filer cannot be unexpected to it. `Sector.is_financial`, the last of the
sector-based gating, is deleted with them.

## Consequences

- No indicator is suppressed for a filer whose chart of accounts supports it. CXSE3's
  three margins are no longer our verdict; they are null because it files revenue = 0, a
  fact of the filing (`zero_denominator`).
- `smaug doctor`: `unexpected_regime` 18 → 0, `zero_denominator` 34 → 52. The count of
  values and `unclassified=0` are unchanged — the same nulls, now attributed to something
  the company filed rather than to a gap between its sector and its schema.
- The `Sector` enum stays, but only as a label (for the UI and the fallback). It no longer
  decides anything.
- A retired null reason still exists in rows computed before this ADR. They are superseded
  by the next `analyze` (the reader only ever loads the latest computation per cell), and
  #71 prunes them.
