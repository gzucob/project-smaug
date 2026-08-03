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
    ``ESPECI`` and a ``DISMES`` restarted at 100 — so nothing downstream would
    restate the older sessions and the joined year would average two share bases.

Both are refused here, and the year they would have completed is reported as a
structural null instead. Refusing costs one cell and keeps every published number
on one base; joining them silently would cost the base itself.
"""

from __future__ import annotations

from collections.abc import Sequence
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


def joins(predecessor: CodeWindow, successor: CodeWindow) -> bool:
    """Whether ``predecessor``'s series can be read as ``successor``'s earlier self."""
    if predecessor.last_session >= successor.first_session:
        return False  # concurrent codes are two listings, not one succession
    if predecessor.last_close <= 0:
        return False
    ratio = successor.first_close / predecessor.last_close
    return _SEAM_FLOOR <= ratio <= _SEAM_CEILING


def chain(
    served: CodeWindow,
    candidates: Sequence[CodeWindow],
    *,
    listed_since: date | None,
) -> tuple[CodeWindow, ...]:
    """``served`` preceded by every code it continues, oldest first.

    Walks backwards one seam at a time from the code that trades today, taking
    the candidate that stops closest to the head of the chain and stopping at the
    first seam the price does not cross. Stopping — rather than skipping to the
    next candidate — is deliberate: a chain with a hole in it is not a series,
    and the hole is what the caller reports.

    ``listed_since`` is the FCA's ``Data_Inicio_Listagem`` for the security, a
    floor and not a birth certificate (``portfolio.domain.listings``). A
    candidate that stops before it belonged to whatever the security was before
    it existed — ALL's ``ALLL3`` for Rumo — and never joins.
    """
    remaining = [
        candidate
        for candidate in candidates
        if candidate.code != served.code
        and (listed_since is None or candidate.last_session >= listed_since)
    ]
    resolved = [served]
    while True:
        head = resolved[0]
        earlier = [c for c in remaining if c.last_session < head.first_session]
        if not earlier:
            return tuple(resolved)
        previous = max(earlier, key=lambda c: c.last_session)
        if not joins(previous, head):
            return tuple(resolved)
        remaining.remove(previous)
        resolved.insert(0, previous)


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
