"""The front-end's indicator docs must not drift from the calculator (#46 / WEB-05).

``frontend/src/lib/indicator-docs.ts`` states every indicator's formula in a
second place and a second language. Nothing but this test enforces the
correspondence: add an indicator to ``Indicators`` without documenting it and the
doc would keep describing the old world with full confidence. Here that drift is
a failing test rather than a silent, user-facing lie.

What is checked mechanically (the reworded-formula prose stays manual, by design
— see the issue) is the **set** of documented indicators, against the
``Indicators`` dataclass and the TypeScript mirror.

Applicability is deliberately *not* checked any more. The front-end used to
restate the calculator's regime guards in a ``naSectors`` field, and this test
kept the copy honest; #54 deleted the copy instead. The API now names the cause
of every null per analysis (``null_reasons``, ADR 0008), so there is one source
and nothing left to drift.
"""

from __future__ import annotations

import re
from pathlib import Path

from smaug.analysis.domain.indicators import indicator_names

_LIB = Path(__file__).parents[1] / "frontend" / "src" / "lib"
_DOCS = (_LIB / "indicator-docs.ts").read_text("utf-8")
_TYPES = (_LIB / "types.ts").read_text("utf-8")


def _typescript_indicator_fields() -> set[str]:
    """The field names of the ``Indicators`` interface in ``types.ts``."""
    block = re.search(r"export interface Indicators \{(.*?)\n\}", _TYPES, re.S)
    assert block is not None, "types.ts has no Indicators interface"
    return set(re.findall(r"^  (\w+): Decimalish;", block.group(1), re.M))


def _documented_indicators() -> set[str]:
    """The top-level keys of ``INDICATOR_DOCS`` (each an ``ind: {`` entry)."""
    return set(re.findall(r"^  (\w+): \{", _DOCS, re.M))


def test_types_mirror_lists_exactly_the_indicator_fields() -> None:
    assert _typescript_indicator_fields() == set(indicator_names())


def test_every_indicator_is_documented_and_no_stragglers() -> None:
    # Adding a field to Indicators without documenting it (or removing one) fails here.
    assert _documented_indicators() == set(indicator_names())
