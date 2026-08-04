"""Sector enum + a coarse fallback from the CVM activity label.

Phase 2 uses the sector to seed the regime fallback when a statement's own
chart of accounts does not say (ADR 0015) — indicator applicability is decided
by the ``filed_regime`` read off the statement itself, not by this enum. Every
ticker resolves its sector the same way, through the CVM FCA registry
(``sector_from_cvm``, fed by ``CvmCompanyRegistry`` — #212): there is no
hand-picked per-ticker override left.
"""

from __future__ import annotations

import unicodedata
from enum import StrEnum


class Sector(StrEnum):
    """Business sector of a ticker, granular enough for Phase 2 criteria."""

    BANK = "bank"
    INSURER = "insurer"
    UTILITY = "utility"
    COMMODITY = "commodity"
    INDUSTRY = "industry"


def _fold(text: str) -> str:
    """Uppercase and strip accents, so substring matches survive 'ç', 'ã', etc."""
    decomposed = unicodedata.normalize("NFKD", text)
    return "".join(c for c in decomposed if not unicodedata.combining(c)).upper()


def sector_from_cvm(cvm_sector: str) -> Sector:
    """Best-effort ``Sector`` from the CVM ``Setor_Atividade`` label.

    The CVM's single activity label (e.g. "Papel e Celulose") folded to the
    five-value enum. It only has to be good enough to seed the display sector
    and the regime *fallback* — indicator applicability is decided by the
    ``filed_regime`` read off the statement itself (ADR 0015), not by this. The
    real B3 taxonomy replaces the enum in a follow-up (M2 taxonomy slice).
    """
    label = _fold(cvm_sector)
    if "BANCO" in label or "INTERMEDIACAO FINANCEIRA" in label:
        return Sector.BANK
    if "SEGUR" in label or "PREVIDENCIA" in label or "CAPITALIZACAO" in label:
        return Sector.INSURER
    # "GAS" is deliberately absent: it collides with "Petróleo e Gás" (a
    # commodity), and gas distribution is rare enough to leave to the default.
    if any(k in label for k in ("ENERGIA ELETRICA", "SANEAMENTO", "AGUA")):
        return Sector.UTILITY
    if any(
        k in label
        for k in ("PETROLEO", "MINERA", "EXTRACAO", "SIDERURGIA", "METALURGIA")
    ):
        return Sector.COMMODITY
    return Sector.INDUSTRY
