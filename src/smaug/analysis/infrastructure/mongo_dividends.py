"""The cash events a ticker's own share class went ex, read off the raw mirror."""

from __future__ import annotations

from collections.abc import Mapping
from datetime import date, timedelta
from decimal import Decimal, InvalidOperation
from typing import Protocol

from smaug.analysis.domain.dividends import CashEvent
from smaug.analysis.infrastructure.mirror import mirror_filter, no_registrant
from smaug.portfolio.domain.company import RegistrantResolver
from smaug.portfolio.domain.share_classes import PerShareClass
from smaug.shared.logging import get_logger

logger = get_logger(__name__)

CASH_DIVIDEND_B3_MODULE = "CASH_DIVIDEND_B3"

# B3 files a payment once per class at rates that differ, so a plain ticker reads
# only its own. A unit asks explicitly for each FCA component class and composes
# the absolute cash rights in the application layer (ADR 0055).
_CLASS_OF_SUFFIX = {"3": "ON", "4": "PN", "5": "PNA", "6": "PNB"}


class RawCollection(Protocol):
    """The subset of a Motor collection this reader uses."""

    def find(self, filter: Mapping[str, object]) -> object: ...


class ValidationCollection(Protocol):
    """The validation lookup used to distinguish an established empty batch."""

    async def find_one(
        self,
        filter: Mapping[str, object],
        *,
        sort: list[tuple[str, int]],
    ) -> Mapping[str, object] | None: ...


class MongoCashEventReader:
    """Serves one ticker's cash events, oldest first, from the B3 mirror."""

    def __init__(
        self,
        collection: RawCollection,
        *,
        registrant_resolver: RegistrantResolver = no_registrant,
        validation_collection: ValidationCollection | None = None,
    ) -> None:
        self._collection = collection
        self._registrant = registrant_resolver
        self._validations = validation_collection

    async def cash_events(
        self, ticker: str, *, per_share_class: PerShareClass | None = None
    ) -> tuple[CashEvent, ...] | None:
        """Every payment ``ticker``'s class went ex, dated by the first session
        that traded without it.

        Deduplicated on (class, ex date, type, value, quotation factor): every
        page of the endpoint is mirrored on every run, so the same payment is
        stored many times over (ADR 0016), and counting one twice would take the
        cash out twice. The quotation factor is load-bearing: B3 can publish the
        same nominal value per share and per lot as distinct rights.
        """
        share_class = (
            per_share_class.value
            if per_share_class is not None
            else _CLASS_OF_SUFFIX.get(ticker.strip()[-1:])
        )
        if share_class is None:
            return ()
        # A quarantined or partially rejected latest batch must hide any mirror
        # rows from analysis. Returning an older/subset history would make a
        # source-validation failure look like a valid distribution history.
        if not await self._batch_is_usable(ticker):
            return None
        cursor = self._collection.find(
            mirror_filter(
                ticker,
                self._registrant,
                source="b3",
                module=CASH_DIVIDEND_B3_MODULE,
            )
        )
        mirrored = False
        seen: dict[tuple[str, str, str, str, str], CashEvent] = {}
        async for document in cursor:  # type: ignore[attr-defined]
            mirrored = True
            payload = document.get("payload")
            if not isinstance(payload, Mapping):
                continue
            if _text(payload.get("share_class")).upper() != share_class:
                continue
            prior = _br_date(payload.get("last_date_prior"))
            percentage = _br_decimal(payload.get("percentage_of_price"))
            value = _br_decimal(payload.get("value"))
            quoted = _br_decimal(payload.get("quoted_per_shares"))
            amount_per_share = (
                value / quoted
                if value is not None and quoted is not None and quoted > 0
                else None
            )
            if prior is None or (
                (percentage is None or percentage <= 0)
                and (amount_per_share is None or amount_per_share <= 0)
            ):
                continue
            key = _cash_event_identity(payload, prior, value, quoted)
            seen[key] = CashEvent(
                # ``lastDatePriorEx`` is the last session that still carried the
                # right, so the price goes ex the day after — the same cut the
                # stock events take (ADR 0034).
                effective=prior + timedelta(days=1),
                percentage=percentage
                if percentage is not None and percentage > 0
                else None,
                amount_per_share=amount_per_share,
                last_with_right=prior,
                approval_date=_br_date(payload.get("approval_date")),
            )
        if not mirrored:
            return () if await self._confirmed_empty(ticker) else None
        return tuple(sorted(seen.values(), key=lambda event: event.effective))

    async def _confirmed_empty(self, ticker: str) -> bool:
        """Whether B3 coverage succeeded and returned zero rows for the company."""
        report = await self._latest_validation(ticker)
        if report is None or report.get("status") != "accepted":
            return False
        observations = report.get("observations")
        return (
            isinstance(observations, Mapping)
            and observations.get("coverage_established") is True
            and observations.get("rows") == 0
        )

    async def _batch_is_usable(self, ticker: str) -> bool:
        """Fail closed when the latest source validation did not admit the batch."""
        report = await self._latest_validation(ticker)
        if report is None:
            # Legacy mirror rows predate durable validation reports. Keep them
            # readable until a new source run supplies an explicit decision.
            return True
        if report.get("status") != "accepted":
            return False
        observations = report.get("observations")
        if not isinstance(observations, Mapping):
            return False
        if observations.get("coverage_established") is False:
            return False
        rejected = observations.get("rejected")
        return not isinstance(rejected, int) or rejected == 0

    async def _latest_validation(self, ticker: str) -> Mapping[str, object] | None:
        """Read the newest validation decision for this ticker's B3 root."""
        if self._validations is None:
            return None
        root = ticker.strip().upper()[:4]
        return await self._validations.find_one(
            {
                "source": "b3",
                "module": CASH_DIVIDEND_B3_MODULE,
                "batch": f"GetListedCashDividends:{root}",
            },
            sort=[("recorded_at", -1)],
        )


def _text(value: object) -> str:
    return str(value).strip() if value is not None else ""


def _cash_event_identity(
    payload: Mapping[str, object],
    prior: date,
    value: Decimal | None,
    quoted: Decimal | None,
) -> tuple[str, str, str, str, str]:
    """Identify one economic B3 cash right, independent of display fields."""
    return (
        _text(payload.get("share_class")).upper(),
        _text(payload.get("event_type")).upper(),
        prior.isoformat(),
        _decimal_identity(value, payload.get("value")),
        _decimal_identity(quoted, payload.get("quoted_per_shares")),
    )


def _decimal_identity(value: Decimal | None, raw: object) -> str:
    """Use parsed B3 numbers when available, retaining unreadable raw values."""
    return str(value.normalize()) if value is not None else _text(raw)


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
