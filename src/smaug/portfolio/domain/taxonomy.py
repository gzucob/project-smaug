"""B3 economic classification — setor → subsetor → segmento.

The B3 groups every listed company in a three-level economic taxonomy. That
taxonomy is a **B3 artifact**, published by the exchange and refreshed weekly —
it is *not* in CVM open data, whose registry carries only a single
``Setor_Atividade`` label (the FCA/cad, see ADR 0023). So the three levels come
from a **committed snapshot**, and a ticker outside it degrades gracefully to the
CVM single level (``subsetor``/``segmento`` unknown) — never an error, never a
blank screen.

The snapshot lives in ``b3_taxonomy.json`` beside this module and is
**generated**, not written: ``smaug taxonomy --check`` reports how it has drifted
from B3, ``--write`` regenerates it. It used to be a dict of fifteen entries
typed in by hand, which was honest while fifteen tickers were analysed and
became a fiction the moment the whole exchange was: 491 of 506 tickers were
falling back to the CVM label, and that fallback answers with 56 cadastral
activity strings ("Emp. Adm. Part. - Sem Setor Principal") where B3 has eleven
economic sectors.

The fallback stays for what B3 does not classify — companies in judicial
recovery, liquidation or bankruptcy, which it drops from the taxonomy while CVM
still registers them.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from functools import cache
from pathlib import Path

TAXONOMY_SNAPSHOT = Path(__file__).with_name("b3_taxonomy.json")


@dataclass(frozen=True)
class Classification:
    """A company's economic classification.

    ``setor`` is always present — the B3 *setor econômico* when the ticker is in
    the snapshot, otherwise the CVM ``Setor_Atividade`` as a single-level
    fallback. ``subsetor``/``segmento`` are ``None`` under that fallback: unknown,
    not inapplicable.
    """

    setor: str
    subsetor: str | None = None
    segmento: str | None = None

    @property
    def source(self) -> str:
        """Where the classification came from: full B3 vs the CVM fallback."""
        return "b3" if self.subsetor is not None else "cvm"


@cache
def _snapshot() -> dict[str, Classification]:
    """The committed B3 snapshot, read once.

    Cached because ``classify`` is called per ticker per exercise — 316k times
    over a whole-exchange doctor run — and the file is small enough that reading
    it once at first use beats any laziness scheme.
    """
    raw = json.loads(TAXONOMY_SNAPSHOT.read_text(encoding="utf-8"))
    return {
        ticker: Classification(levels[0], levels[1], levels[2])
        for ticker, levels in raw["tickers"].items()
    }


def snapshot_tickers() -> frozenset[str]:
    """Which tickers the committed snapshot covers."""
    return frozenset(_snapshot())


def b3_classification(ticker: str) -> Classification | None:
    """The full B3 three-level classification for ``ticker``, if in the snapshot."""
    return _snapshot().get(ticker.upper().strip())


def classify(ticker: str, cvm_sector: str | None) -> Classification | None:
    """Resolve a ticker's classification: B3 snapshot, else the CVM fallback.

    Returns ``None`` only when neither is available (an unknown ticker) — the
    caller turns that into ``UnknownTickerError``. When only ``cvm_sector`` is
    known, ``subsetor``/``segmento`` stay ``None`` (single-level fallback).
    """
    snapshot = b3_classification(ticker)
    if snapshot is not None:
        return snapshot
    if cvm_sector:
        return Classification(cvm_sector)
    return None
