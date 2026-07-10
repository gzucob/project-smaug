# Documentation Rules

## One fact, one place

Four artifacts carry the project's written knowledge. Each answers exactly one
question, and each has its own lifecycle. Mixing them is what broke
`docs/FINDINGS_INDICATORS.md` (see #43): it tried to be all four at once, and
the fastest-ageing part — the state of the data — silently rotted the rest.

| Artifact | Answers | Lifecycle |
|---|---|---|
| `.claude/RULES/` | *How we work* | Durable; changes by decision |
| `docs/adr/NNNN-*.md` | *Why we chose* | **Immutable** — superseded, never edited |
| GitHub issue | *What is left* | Closed when a verifiable acceptance criterion is met |
| Generated report | *What is true right now* | Never hand-written; produced by a command |

Before writing a paragraph anywhere, ask which of the four it is. If it is two
of them, it is two artifacts.

## Rules (`.claude/RULES/`)

Durable engineering conventions — how to branch, type, test, lay out a context.
Prescriptive and in the present tense. A rules file describes what the codebase
*actually does today*, not an aspiration; when the code and a rule disagree, the
code is the source of truth and the rule gets fixed.

Every rules file is listed in the Rules Index table in `CLAUDE.md`. Adding a
file without adding its row means nothing will read it.

## ADRs (`docs/adr/NNNN-title-in-kebab-case.md`)

An ADR records **why a choice was made**, once, at the moment it was made.

- **Immutable.** Never edit an ADR to reflect later learning. If the decision
  changes, write a new ADR that supersedes it, and add a `Superseded by`
  line to the old one's status — that single line is the only permitted edit.
- `NNNN` is a zero-padded sequence (`0001`, `0002`, …). Never reused.
- **No status of the world.** An ADR never says "currently null for 7 tickers",
  never links a table of live values, never carries a to-do. Those are a
  generated report and an issue, respectively.

Structure:

```markdown
# NNNN — Short decision title

- **Status:** Accepted | Superseded by [NNNN](NNNN-....md)
- **Date:** YYYY-MM-DD

## Context
The forces at play: what we knew, what constrained us.

## Decision
What we chose, stated in the present tense.

## Consequences
What this buys, what it costs, and what it rules out. Both directions.
```

Write an ADR when a choice is (a) hard to reverse, (b) surprising to a reader
of the code, or (c) something a future session would otherwise re-litigate.
A flat 34% tax rate in ROIC is an ADR. A variable rename is not.

## Issues

The only place work-that-is-left lives. A finding buried in prose has no state:
it cannot be assigned, closed, or counted. Three findings died that way (#43).

- **A follow-up discovered while writing an ADR becomes an issue** in the same
  session, before the session ends. The ADR states the decision; the issue
  carries the doubt.
- Format, namespaces, and required labels: `.claude/RULES/RULES_ISSUES.md`.
- Every issue closes against a **verifiable acceptance criterion** — a command
  that passes, a report row that turns green — not "looks right now".

## Generated reports

Anything that describes the current state of the data — coverage, nulls, a
divergence from a reference platform — is **produced by a command**, never
typed by hand. Hand-written state is stale the moment it is committed, and
nothing recalculates prose.

- Coverage of the persisted analysis: `smaug doctor` (the M0 gate, #43).
- Fidelity against the reference platforms: a committed fixture of the
  platforms' values plus a test with a per-indicator tolerance. A divergence is
  a **failing test**, not a paragraph.

A generated report is not committed unless a command can regenerate it byte for
byte.

## Retired documents

Three documents were deleted in #43 rather than frozen, because a frozen document
still gets read as current by whoever finds it first:

- `docs/FINDINGS_INDICATORS.md` — the findings log. Its decisions became ADRs
  0001–0005; its follow-ups became issues #44, #45, #46; its data snapshots were
  already stale and are now regenerated.
- `docs/PLANO_FASE1.md` — the Phase 1 plan. Its post-implementation section
  became ADR 0006.
- `docs/preview_fase1_criterios_implementacao.md` — the Phase 1 criteria. Its
  surviving principles are in `CLAUDE.md`'s *What NOT to Do*.

They remain in git history. **Before deleting a document, migrate what is
load-bearing out of it** — a decision to an ADR, a doubt to an issue, a fact
about the data to a code comment or a test. Deletion is cheap to reverse and
expensive to notice: nobody runs `git log --follow` on a file they do not know
existed.

## Language

Documentation prose may be PT-BR (`docs/ROADMAP.md` and user-facing text) or
English. **ADRs, rules files, issues, commits, and PRs are English** — same as
the project-wide rule in `CLAUDE.md`.
