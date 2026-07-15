"""The front-end's indicator docs must not drift from the calculator (#46 / WEB-05).

``frontend/src/lib/indicator-docs.ts`` states every indicator's formula and its
applicable sectors in a second place and a second language. Nothing but this test
enforces the correspondence: add an indicator to ``Indicators`` without documenting
it, or change a regime's applicability without updating ``naSectors``, and the doc
would keep describing the old world with full confidence. Here that drift is a
failing test rather than a silent, user-facing lie.

Two things are checked mechanically (the reworded-formula prose stays manual, by
design — see the issue): the **set** of documented indicators, against the
``Indicators`` dataclass and the TypeScript mirror; and each indicator's
``naSectors``, against the calculator's own ``_INAPPLICABLE_BY_REGIME`` (mapped
through the regime each sector is expected to file under).
"""

from __future__ import annotations

import re
from pathlib import Path

from smaug.analysis.domain.calculator import _INAPPLICABLE_BY_REGIME
from smaug.analysis.domain.financials import expected_regime
from smaug.analysis.domain.indicators import indicator_names
from smaug.portfolio.domain.sectors import Sector

_LIB = Path(__file__).parents[1] / "frontend" / "src" / "lib"
_DOCS = (_LIB / "indicator-docs.ts").read_text("utf-8")
_TYPES = (_LIB / "types.ts").read_text("utf-8")

# ``naSectors: FINANCIAL`` is the shorthand the file defines for the two financial
# sectors; resolve it the same way TypeScript does.
_FINANCIAL = ("bank", "insurer")


def _typescript_indicator_fields() -> set[str]:
    """The field names of the ``Indicators`` interface in ``types.ts``."""
    block = re.search(r"export interface Indicators \{(.*?)\n\}", _TYPES, re.S)
    assert block is not None, "types.ts has no Indicators interface"
    return set(re.findall(r"^  (\w+): Decimalish;", block.group(1), re.M))


def _documented_indicators() -> set[str]:
    """The top-level keys of ``INDICATOR_DOCS`` (each an ``ind: {`` entry)."""
    return set(re.findall(r"^  (\w+): \{", _DOCS, re.M))


def _documented_na_sectors() -> dict[str, set[str]]:
    """Each documented indicator's ``naSectors``, resolving the FINANCIAL alias."""
    na: dict[str, set[str]] = {}
    current: str | None = None
    for line in _DOCS.splitlines():
        key = re.match(r"^  (\w+): \{", line)
        if key is not None:
            current = key.group(1)
            na.setdefault(current, set())  # no naSectors line -> applicable everywhere
        if "naSectors:" in line and current is not None:
            na[current] = (
                set(_FINANCIAL)
                if "FINANCIAL" in line
                else set(re.findall(r'"(\w+)"', line))
            )
    return na


def _expected_na_sectors(indicator: str) -> set[str]:
    """Sectors for which the calculator suppresses ``indicator`` by regime.

    Keyed on the regime each sector is *expected* to file under (``expected_regime``),
    which is the sector-level view ``naSectors`` documents — the same mapping the old
    ``is_financial`` guard approximated, now sourced from ``_INAPPLICABLE_BY_REGIME``.
    """
    na: set[str] = set()
    for sector in Sector:
        inapplicable = _INAPPLICABLE_BY_REGIME.get(expected_regime(sector), frozenset())
        if indicator in inapplicable:
            na.add(sector.value)
    return na


def test_types_mirror_lists_exactly_the_indicator_fields() -> None:
    assert _typescript_indicator_fields() == set(indicator_names())


def test_every_indicator_is_documented_and_no_stragglers() -> None:
    # Adding a field to Indicators without documenting it (or removing one) fails here.
    assert _documented_indicators() == set(indicator_names())


def test_documented_applicability_matches_the_calculator() -> None:
    # Changing a regime's inapplicable set without updating naSectors fails here — the
    # drift that puts a wrong "n/d" in front of a user.
    documented = _documented_na_sectors()
    mismatches = {
        indicator: (documented.get(indicator, set()), _expected_na_sectors(indicator))
        for indicator in indicator_names()
        if documented.get(indicator, set()) != _expected_na_sectors(indicator)
    }
    assert not mismatches, f"naSectors drift (doc, calculator): {mismatches}"
