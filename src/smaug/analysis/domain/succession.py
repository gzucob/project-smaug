"""Which codes answer for one security, and when a year cannot be answered at all.

B3 files a year's quotes under the code that traded that year, so a security that
changed code has its history split across codes and the analysis reads only the
last piece (#193). Joining the pieces is not a corporate action — no ratio, no ex
date, nothing to restate (ADRs 0033-0038). It only decides *which sessions exist*,
and the restatement then applies to them unchanged, because it is dated session by
session.

What makes the join safe is that it is refused wherever the evidence is thin.
Two codes belong to one series only when the price **crosses the seam**: measured
over 25 successions the price carries over on the very next session, between
×0.911 and ×1.061, and the two that do not are precisely the two that must never
be joined —

  * ``ALLL3`` → ``RUMO3`` (×0.343): a share exchange. Rumo's registrant is ALL's
    old CNPJ, so "same company" would drag in a series quoted on another share
    entirely; the market says it is another share and the FCA agrees, dating the
    security from the day the combination closed.
  * ``LLIS3`` → ``VSTE3`` (×7.474): a grupamento executed with the rename. B3's
    tape says nothing about it — the successor's first session carries a clean
    ``ESPECI`` and a ``DISMES`` restarted at 100 — so on its own nothing
    downstream would restate the older sessions and the joined year would average
    two share bases.

The first is refused outright: no ratio restates a share exchange, because the
holder's claim itself was swapped. The second is refused only while it is
unexplained — the seam is offered to the restatement chain as the date of an
action, and where the chain takes it (the ratio being CVM's own declaration,
ADR 0043) the older sessions are restated like any others and the join is
arithmetic. A seam nothing explains still stops the chain, and the year it would
have completed is reported as a structural null instead.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import date
from decimal import Decimal

# How far the price may move across a seam and still be the same share. Wide,
# because it separates an ordinary overnight move from a change of base rather
# than measuring either: the successions that are real sit inside ±6%, and the
# two that are not sit at ×0.34 and ×7.5. It is a tripwire, never a ratio — the
# size of an action comes from the filed counts, never from the tape (ADR 0035).
_SEAM_FLOOR = Decimal("0.8")
_SEAM_CEILING = Decimal("1.25")

# What share of a year's remaining sessions a code has to print, from its first
# one, for that first session to read as the code's *debut* rather than as an
# illiquid share's first trade of the year. Measured on both populations: a code
# that debuted mid-year trades essentially every session after it (COGN3 53 of
# 53, EGIE3 112 of 112, RAIL3 199 of ~200, JPSA3 202 of ~210), while an illiquid
# one prints a handful across the whole year (AHEB5 5, BDLL3 1, CRPG3 8). The
# gap between the two populations is wide enough that the threshold sits in
# empty space.
_DEBUT_COVERAGE = Decimal("0.5")


@dataclass(frozen=True, slots=True)
class CodeWindow:
    """One trading code's life inside the years being read, and its two ends."""

    code: str
    first_session: date
    last_session: date
    first_close: Decimal
    last_close: Decimal


def crosses(before: Decimal, after: Decimal) -> bool:
    """Whether a price carries over between two consecutive sessions unchanged.

    The one place the seam band is applied, because two readers ask the same
    question of it: the chain, deciding whether to join, and the tape reader,
    deciding whether the seam is a session worth offering the restatement as a
    date it has for nothing else.
    """
    if before <= 0 or after <= 0:
        return False
    return _SEAM_FLOOR <= after / before <= _SEAM_CEILING


def adjacent(predecessor: CodeWindow, successor: CodeWindow) -> bool:
    """Whether ``predecessor`` stops where ``successor`` starts."""
    return predecessor.last_session < successor.first_session


def joins(predecessor: CodeWindow, successor: CodeWindow) -> bool:
    """Whether ``predecessor``'s series can be read as ``successor``'s earlier self."""
    return adjacent(predecessor, successor) and crosses(
        predecessor.last_close, successor.first_close
    )


Explains = Callable[[CodeWindow, CodeWindow], bool]


def _explains_nothing(predecessor: CodeWindow, successor: CodeWindow) -> bool:
    return False


def candidates_of(
    served: CodeWindow,
    siblings: Sequence[CodeWindow],
    *,
    listed_since: date | None,
) -> tuple[CodeWindow, ...]:
    """Every code that could be ``served``'s earlier self, oldest first.

    Adjacency and the listing floor only — the two conditions that are facts
    about the cadastre and the calendar. Whether the *price* carries across each
    seam is a separate question, asked by ``chain`` below, because the tape
    reader needs to walk even the seams the price side will refuse: a seam is the
    only witness to the date of an action nothing else dates (ADR 0043).

    ``listed_since`` is the FCA's ``Data_Inicio_Listagem`` for the security, a
    floor and not a birth certificate (``portfolio.domain.listings``). A
    candidate that stops before it belonged to whatever the security was before
    it existed — ALL's ``ALLL3`` for Rumo — and is never one of these.
    """
    remaining = [
        candidate
        for candidate in siblings
        if candidate.code != served.code
        and (listed_since is None or candidate.last_session >= listed_since)
    ]
    resolved = [served]
    while True:
        head = resolved[0]
        earlier = [c for c in remaining if adjacent(c, head)]
        if not earlier:
            return tuple(resolved)
        previous = max(earlier, key=lambda c: c.last_session)
        remaining.remove(previous)
        resolved.insert(0, previous)


def joined(
    candidates: Sequence[CodeWindow], *, explains: Explains = _explains_nothing
) -> tuple[CodeWindow, ...]:
    """The candidates that read as one series, oldest first.

    Walks backwards one seam at a time from the code that trades today and stops
    at the first seam that is neither crossed by the price nor explained by the
    restatement. Stopping — rather than skipping to the next candidate — is
    deliberate: a chain with a hole in it is not a series, and the hole is what
    the caller reports.

    ``explains`` answers whether the share base moved on that seam and the move
    is already dated, in which case the older sessions are restated onto today's
    base like any others and joining them is arithmetic rather than a guess
    (ADR 0043). It is what recovers Le Lis Blanc: its 8:1 grupamento took effect
    on the very session ``VSTE3`` replaced ``LLIS3``.
    """
    for index in range(len(candidates) - 1, 0, -1):
        predecessor, successor = candidates[index - 1], candidates[index]
        if not joins(predecessor, successor) and not explains(predecessor, successor):
            return tuple(candidates[index:])
    return tuple(candidates)


def chain(
    served: CodeWindow,
    siblings: Sequence[CodeWindow],
    *,
    listed_since: date | None,
    explains: Explains = _explains_nothing,
) -> tuple[CodeWindow, ...]:
    """``served`` preceded by every code it continues, oldest first."""
    return joined(
        candidates_of(served, siblings, listed_since=listed_since), explains=explains
    )


def structural_gap(
    year: int,
    *,
    chain_start: date | None,
    listed_since: date | None,
    year_opened_on: date | None,
    coverage: Decimal | None,
) -> bool:
    """Whether ``year`` is a year the named codes cannot price.

    Two shapes of the same fact, both of them "the security was trading and we
    cannot say under what": a chain that starts after the year, and — the silent
    one — a chain that starts *inside* it. That last one is served today as an
    average of the sessions that happen to be there, which is how AMER3's 2021
    comes out 31.7% below the year the market actually traded (114 of 247
    sessions) with nothing to show for it.

    A code with no session anywhere in the window is deliberately **not** one of
    them: BAUH3 has been listed since before the archive and has never traded a
    session, which is a fact about the market rather than a code we failed to
    name (#164). It keeps the plain missing-price null it has today.

    Never claimed against a security younger than the year either: an IPO's first
    year is legitimately a part-year, and ``listed_since`` is exactly the column
    that says so (#153).
    """
    if listed_since is None or listed_since >= date(year, 1, 1):
        return False
    if chain_start is None:
        return False
    if chain_start.year > year:
        return True
    if chain_start.year < year:
        return False
    if year_opened_on is not None and chain_start <= year_opened_on:
        return False  # the chain covers the year from its first session
    return coverage is not None and coverage >= _DEBUT_COVERAGE
