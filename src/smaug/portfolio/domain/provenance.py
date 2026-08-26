"""Provenance value objects for portfolio identity data."""

from __future__ import annotations

from dataclasses import dataclass

FCA_SOURCE = "cvm_fca"


@dataclass(frozen=True, slots=True)
class FcaSnapshotProvenance:
    """The immutable source identity used for current FCA resolution."""

    year: int
    source: str
    source_url: str
    artifact_id: str | None = None
