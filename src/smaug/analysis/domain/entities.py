"""Analysis domain entity: the computed result for one ticker."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from typing import Literal

from smaug.analysis.domain.financials import (
    AccountingRegime,
    ClassMarketValue,
    DebtCoverageEvidence,
    DebtEvidenceSnapshot,
    RegimeSource,
    ShareCountProvenance,
)
from smaug.analysis.domain.indicators import Indicators
from smaug.portfolio.domain.share_classes import ShareClassMapping
from smaug.portfolio.domain.taxonomy import Classification

# The two analysis perspectives the system produces for each ticker:
# the live trailing-twelve-months snapshot and one row per closed fiscal year.
AnalysisView = Literal["ttm_live", "closed_year"]
VIEW_TTM: AnalysisView = "ttm_live"
VIEW_CLOSED_YEAR: AnalysisView = "closed_year"


@dataclass(frozen=True)
class PruneResult:
    """Outcome of pruning superseded analysis runs (#71).

    ``deleted`` is how many stale rows were removed; ``kept`` is how many latest-
    per-(ticker, view, reference_date) rows remain — after a prune this equals the
    number of distinct cells the reads already surface.
    """

    deleted: int
    kept: int


@dataclass(frozen=True)
class TickerAnalysis:
    """Indicators for one ticker, tagged with the inputs' provenance."""

    ticker: str
    # The B3 economic taxonomy (setor → subsetor → segmento), or the CVM
    # single-level fallback for a ticker outside the snapshot (ADR 0024). Replaces
    # the old five-value ``Sector`` enum, which survives only as an internal
    # regime hint on ``StandardizedFinancials``.
    classification: Classification
    reference_date: date  # CVM period the fundamentals came from
    computed_at: datetime
    indicators: Indicators
    # The price the market multiples divide by: B3's last available close for the
    # view's valuation date. A closed year no longer mixes a mean price with a
    # closing share count (ADR 0057).
    price: Decimal | None = None
    # The same year's dividend-adjusted average: a total-return ruler, not a
    # valuation one. Kept for return comparisons; ``None`` for the live view, which
    # has had no payout since to adjust for.
    price_adjusted: Decimal | None = None
    price_basis: str | None = None  # how ``price`` and every class cap price derive
    # Exact bases behind the other stock inputs. These remain populated on a null
    # result: they describe the requested calculation, not whether its source row
    # happened to be available.
    share_count_basis: str | None = None
    liquidity_basis: str | None = None
    debt_basis: str | None = None
    roic_tax_basis: str | None = None
    view: AnalysisView = VIEW_TTM  # which perspective this row represents
    # Filing-derived regime provenance. ``None`` on legacy rows predating the
    # provenance migration; newly analyzed rows always carry both values.
    filed_regime: AccountingRegime | None = None
    regime_source: RegimeSource | None = None
    # Debt evidence identity and raw-BPP decision. New analysis rows carry this
    # even when the identity is explicitly unknown; pre-migration rows do not.
    issuer_name: str | None = None
    cd_cvm: str | None = None
    cnpj: str | None = None
    debt_evidence: DebtCoverageEvidence | None = None
    debt_evidence_snapshot: DebtEvidenceSnapshot | None = None
    # FCA class identity and historical code evidence used by the market-cap
    # calculation. Empty on legacy rows created before #259.
    share_class_mappings: tuple[ShareClassMapping, ...] = ()
    class_market_values: tuple[ClassMarketValue, ...] = ()
    capital_provenance: ShareCountProvenance | None = None
