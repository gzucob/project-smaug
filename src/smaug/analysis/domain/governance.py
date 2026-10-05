"""Dated listing classification and security-specific minority protection."""

from dataclasses import dataclass
from datetime import date
from decimal import Decimal


@dataclass(frozen=True)
class Governance:
    """Selected evidence, independent of the company's economic sector."""

    # CVM FCA ``Data_Inicio_Listagem``, exposed as the company's IPO date.
    ipo_date: date | None = None
    listing_segment: str | None = None
    listing_observed_on: date | None = None
    listing_source: str | None = None
    tag_along: Decimal | None = None
    tag_along_source: str | None = None
    tag_along_reference: str | None = None
    blocker: str | None = "missing_tag_along_evidence"


def segment_tag_along(segment: str | None, classes: tuple[str, ...]) -> Decimal | None:
    """Resolve only guarantees that establish the percentage for every component."""
    if not classes:
        return None
    if segment == "novo_mercado" and set(classes) == {"ON"}:
        return Decimal(1)
    if segment in {"nivel_2", "bovespa_mais_nivel_2"} and all(
        kind in {"ON", "PN", "PNA", "PNB", "PNC", "PND"} for kind in classes
    ):
        return Decimal(1)
    if segment == "bovespa_mais" and set(classes) == {"ON"}:
        return Decimal(1)
    return None
