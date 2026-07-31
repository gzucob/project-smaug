"""Refresh the committed B3 taxonomy snapshot, or report how it has drifted.

B3 republishes the classification weekly, so the snapshot is stale by
construction — the question is never "is it current" but "has anything moved,
and does the move need a human". Hence two modes over one comparison: report the
drift and change nothing, or write it down.

What counts as drift is deliberately more than "the file differs". A ticker that
gained a classification, one that lost it, and one whose sector *changed* are
three different pieces of news — the last being the only one that silently
restates history, since every stored analysis carries the classification it was
computed under.
"""

from __future__ import annotations

import json
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path

from smaug.portfolio.domain.taxonomy import Classification, b3_classification
from smaug.shared.logging import get_logger

logger = get_logger(__name__)


@dataclass(frozen=True)
class TaxonomyDrift:
    """How the fetched classification differs from the committed snapshot."""

    gained: tuple[str, ...]  # ticker now classified that was not
    lost: tuple[str, ...]  # ticker in the snapshot that B3 no longer classifies
    changed: tuple[tuple[str, Classification, Classification], ...]
    unchanged: int
    unclassified: tuple[str, ...]  # companies B3 answered nothing for
    unknown_labels: tuple[str, ...]  # a label outside B3's own vocabulary
    from_sheet: int = 0  # tickers named by the spreadsheet
    from_detail: int = 0  # tickers only the per-company fallback could reach

    @property
    def moved(self) -> bool:
        """Whether anything at all differs — what ``--check`` exits on."""
        return bool(self.gained or self.lost or self.changed or self.unknown_labels)


def compare(
    fetched: dict[str, Classification],
) -> tuple[
    tuple[str, ...],
    tuple[str, ...],
    tuple[tuple[str, Classification, Classification], ...],
    int,
]:
    """Split the fetched classifications against the snapshot."""
    gained: list[str] = []
    changed: list[tuple[str, Classification, Classification]] = []
    unchanged = 0
    for ticker, classification in sorted(fetched.items()):
        current = b3_classification(ticker)
        if current is None:
            gained.append(ticker)
        elif current != classification:
            changed.append((ticker, current, classification))
        else:
            unchanged += 1
    return tuple(gained), (), tuple(changed), unchanged


def snapshot_payload(fetched: dict[str, Classification]) -> str:
    """The snapshot file's exact bytes, so a re-run reproduces them.

    Sorted, two-space indented, UTF-8 without escapes: the file is read by people
    reviewing a weekly diff, and `\\u00e1` in place of `á` would make every line
    of it unreadable for the sake of nothing.
    """
    payload = {
        "_generated_by": "smaug taxonomy --write",
        "_source": "B3 Classificação Setorial, via the listed-company detail API",
        "tickers": {
            ticker: [c.setor, c.subsetor, c.segmento]
            for ticker, c in sorted(fetched.items())
        },
    }
    return json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=False) + "\n"


class RefreshTaxonomyUseCase:
    """Compare B3's current classification against the committed snapshot."""

    def __init__(self, snapshot_path: Path) -> None:
        self._path = snapshot_path

    def drift(
        self,
        fetched: dict[str, Classification],
        *,
        unclassified: Iterable[str] = (),
        unknown_labels: Iterable[str] = (),
        from_sheet: int = 0,
        from_detail: int = 0,
    ) -> TaxonomyDrift:
        gained, _, changed, unchanged = compare(fetched)
        lost = tuple(
            sorted(t for t in _snapshot_tickers(self._path) if t not in fetched)
        )
        return TaxonomyDrift(
            gained=gained,
            lost=lost,
            changed=changed,
            unchanged=unchanged,
            unclassified=tuple(unclassified),
            unknown_labels=tuple(unknown_labels),
            from_sheet=from_sheet,
            from_detail=from_detail,
        )

    def write(self, fetched: dict[str, Classification]) -> int:
        """Rewrite the snapshot; returns how many tickers it now covers."""
        self._path.write_text(snapshot_payload(fetched), encoding="utf-8")
        logger.info("Wrote %d tickers to %s", len(fetched), self._path.name)
        return len(fetched)


def _snapshot_tickers(path: Path) -> frozenset[str]:
    if not path.exists():
        return frozenset()
    return frozenset(json.loads(path.read_text(encoding="utf-8"))["tickers"])
