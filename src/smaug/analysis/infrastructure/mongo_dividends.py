"""The cash events a ticker's own share class went ex, read off the raw mirror."""

from __future__ import annotations

from collections.abc import Mapping
from datetime import date, timedelta
from decimal import Decimal, InvalidOperation
from typing import Protocol

from smaug.analysis.domain.dividends import CashEvent
from smaug.analysis.infrastructure.mirror import mirror_filter, no_registrant
from smaug.portfolio.domain.company import RegistrantResolver
from smaug.shared.logging import get_logger

logger = get_logger(__name__)

CASH_DIVIDEND_B3_MODULE = "CASH_DIVIDEND_B3"

# B3 files a payment once per class at rates that differ, so a ticker reads only
# its own. A unit bundles both and B3 files no rate for the bundle; its per-share
# indicators are already null for the same reason (#38), so it reads nothing here.
_CLASS_OF_SUFFIX = {"3": "ON", "4": "PN", "5": "PNA", "6": "PNB"}


class RawCollection(Protocol):
    """The subset of a Motor collection this reader uses."""

    def find(self, filter: Mapping[str, object]) -> object: ...


class MongoCashEventReader:
    """Serves one ticker's cash events, oldest first, from the B3 mirror."""

    def __init__(
        self,
        collection: RawCollection,
        *,
        registrant_resolver: RegistrantResolver = no_registrant,
    ) -> None:
        self._collection = collection
        self._registrant = registrant_resolver

    async def cash_events(self, ticker: str) -> tuple[CashEvent, ...]:
        """Every payment ``ticker``'s class went ex, dated by the first session
        that traded without it.

        Deduplicated on (class, ex date, type, value): every page of the endpoint
        is mirrored on every run, so the same payment is stored many times over
        (ADR 0016), and counting one twice would take the cash out twice.
        """
        share_class = _CLASS_OF_SUFFIX.get(ticker.strip()[-1:])
        if share_class is None:
            return ()
        cursor = self._collection.find(
            mirror_filter(ticker, self._registrant, module=CASH_DIVIDEND_B3_MODULE)
        )
        seen: dict[tuple[str, str, str], CashEvent] = {}
        async for document in cursor:  # type: ignore[attr-defined]
            payload = document.get("payload")
            if not isinstance(payload, Mapping):
                continue
            if _text(payload.get("share_class")).upper() != share_class:
                continue
            prior = _br_date(payload.get("last_date_prior"))
            percentage = _br_decimal(payload.get("percentage_of_price"))
            if prior is None or percentage is None or percentage <= 0:
                # A row B3 leaves without a percentage is one whose value rounds
                # to nothing (``0,0000000001``); skipping it costs no cash.
                continue
            key = (
                _text(payload.get("last_date_prior")),
                _text(payload.get("event_type")),
                _text(payload.get("value")),
            )
            seen[key] = CashEvent(
                # ``lastDatePriorEx`` is the last session that still carried the
                # right, so the price goes ex the day after — the same cut the
                # stock events take (ADR 0034).
                effective=prior + timedelta(days=1),
                percentage=percentage,
            )
        return tuple(sorted(seen.values(), key=lambda event: event.effective))


def _text(value: object) -> str:
    return str(value).strip() if value is not None else ""


def _br_decimal(value: object) -> Decimal | None:
    raw = _text(value)
    if not raw:
        return None
    try:
        return Decimal(raw.replace(".", "").replace(",", "."))
    except InvalidOperation:
        return None


def _br_date(value: object) -> date | None:
    raw = _text(value)
    try:
        day, month, year = (int(part) for part in raw.split("/"))
    except ValueError:
        return None
    try:
        return date(year, month, day)
    except ValueError:
        return None
