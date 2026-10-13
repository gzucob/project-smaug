"""Compact persisted analysis fields for home rankings and portfolio cards."""

from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import Protocol

from smaug.portfolio.domain.taxonomy import Classification


@dataclass(frozen=True)
class AnalysisSummary:
    """One current TTM result without its statement and evidence payloads."""

    ticker: str
    reference_date: date
    classification: Classification
    issuer: str | None
    listing_segment: str | None
    price: Decimal | None
    price_as_traded: Decimal | None
    market_cap: Decimal | None
    roe: Decimal | None
    dividend_yield: Decimal | None
    pe_basic: Decimal | None


class AnalysisSummaryReader(Protocol):
    """Read compact current analyses without reconstructing financial evidence."""

    async def all_latest(self) -> list[AnalysisSummary]: ...
