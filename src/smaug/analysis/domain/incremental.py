"""Input cursors and the scope of an incremental publication."""

from collections.abc import Mapping
from dataclasses import dataclass, field

from smaug.analysis.domain.price_refresh import PriceRefreshIssue


@dataclass(frozen=True)
class RefreshCheckpoint:
    raw_id: str | None
    archives: Mapping[str, str]
    retry_tickers: frozenset[str] = frozenset()


@dataclass(frozen=True)
class RefreshScope:
    issuers: frozenset[str]
    compare_prices: bool
    rejected_price_codes: frozenset[str] = frozenset()
    unscoped_price_rejection: bool = False
    previously_rejected_codes: frozenset[str] = frozenset()
    previously_unscoped_price_rejection: bool = False
    retry_tickers: frozenset[str] = frozenset()
    ingestion_errors: Mapping[str, str] = field(default_factory=dict)

    @property
    def price_issues(self) -> tuple[PriceRefreshIssue, ...]:
        symbols = set(self.rejected_price_codes)
        if self.unscoped_price_rejection:
            symbols.add("B3")
        return tuple(
            PriceRefreshIssue(code, "record_rejected") for code in sorted(symbols)
        )
