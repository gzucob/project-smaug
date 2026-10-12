"""CVM-declared shareholder positions and their filing provenance."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal


@dataclass(frozen=True, slots=True)
class ShareholderClassPosition:
    """One shareholder's CVM-filed position in a named share class."""

    share_class: str
    shares: Decimal | None
    percent: Decimal | None


@dataclass(frozen=True, slots=True)
class ShareholderPosition:
    """One direct or related shareholder row from a CVM reference form."""

    shareholder_id: str
    name: str
    shareholder_type: str | None
    related_shareholder_id: str | None
    is_controller: bool | None
    ordinary_shares: Decimal | None
    ordinary_percent: Decimal | None
    preferred_shares: Decimal | None
    preferred_percent: Decimal | None
    total_shares: Decimal | None
    total_percent: Decimal | None
    last_change_date: date | None
    class_positions: tuple[ShareholderClassPosition, ...]


@dataclass(frozen=True, slots=True)
class ShareholdingSnapshot:
    """One selected FRE filing, retaining its date, version, and source archive."""

    reference_date: date
    version: int
    document_id: str
    archive_year: int
    artifact_id: str | None
    positions: tuple[ShareholderPosition, ...]

    @property
    def direct_positions(self) -> tuple[ShareholderPosition, ...]:
        """Rows that directly name holders in the issuer's reported capital table."""
        return tuple(
            position
            for position in self.positions
            if position.related_shareholder_id is None
        )
