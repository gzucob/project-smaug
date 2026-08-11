"""Reads the capital raw mirror (Mongo) into per-year **outstanding** share counts.

Two filings, read together, because neither says it all:

* ``CAPITAL`` — the FRE, which files the shares **issued**, split by class (ADR
  0004). The primary count.
* ``CAPITAL_DFP`` — the statements' own ``composicao_capital``, the only filing
  that names the shares held **in treasury** (ADR 0016). Those are issued but not
  outstanding, and the counts served here are net of them (ADR 0017).

The analysis needs a count for each view: the fiscal year of a closed-year
analysis, and the current year for the live TTM. A year that was never ingested
falls back to the nearest *earlier* year on file — share counts move slowly, and
an adjacent year beats no indicator at all. A year with nothing before it yields
``None``.

Two readings of the same filing, for two different jobs:

* ``counts`` — the classes, which the market cap sums price by price (ADR 0014).
  Served for every ticker, units included: the cap needs the underlying classes
  precisely because a unit's quote prices a bundle.
* ``outstanding`` — the total, the denominator of the per-share indicators
  (LPA/VPA) alone. A unit divides that underlying total by its FCA bundle count;
  when the FCA identifies a unit but its composition is unreadable, the result
  is null rather than silently reverting to the underlying-share denominator.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from datetime import date, timedelta
from decimal import Decimal, InvalidOperation
from typing import Any, Protocol

from smaug.analysis.domain.capital import (
    BaseChange,
    CorporateAction,
    ExchangeAction,
    RestatementStep,
    filed_scale,
    outstanding_counts,
    restatement_factors,
    restatement_timeline,
)
from smaug.analysis.domain.financials import CapitalComposition, ShareCounts
from smaug.analysis.domain.indicators import NullReason
from smaug.analysis.domain.ports import BaseChangeReader
from smaug.analysis.infrastructure.mirror import mirror_filter, no_registrant
from smaug.portfolio.domain.company import RegistrantResolver, UnitResolver, no_units
from smaug.shared.logging import get_logger

logger = get_logger(__name__)

CAPITAL_MODULE = "CAPITAL"
TREASURY_MODULE = "CAPITAL_DFP"
CAPITAL_EVENT_MODULE = "CAPITAL_EVENT"
CAPITAL_EVENT_B3_MODULE = "CAPITAL_EVENT_B3"


class RawCollection(Protocol):
    """Minimal read surface over the ``raw_ingestions`` collection."""

    def find(self, filter: Mapping[str, Any], /) -> Any: ...


def _dec(value: Any) -> Decimal | None:
    if value is None:
        return None
    try:
        return Decimal(str(value))
    except (InvalidOperation, ValueError):
        return None


def _positive(value: Any) -> Decimal | None:
    """A share count that can serve as a denominator, else ``None``.

    The FRE writes a class the company does not have as ``0`` (every single-class
    filer writes zero preferred shares), and zero shares is never a fact to divide
    by — it is an absence. Naming it ``None`` here keeps it out of the cap.
    """
    count = _dec(value)
    return count if count is not None and count > 0 else None


def _upper(value: Any) -> str:
    return str(value).strip().upper() if value is not None else ""


def _br_decimal(value: Any) -> Decimal | None:
    """A number as B3 writes it: ``7.900,00000000000`` is seven thousand nine hundred.

    The thousands separator is the period and the decimal is the comma.
    """
    if value is None:
        return None
    text = str(value).strip().replace(".", "").replace(",", ".")
    if not text:
        return None
    try:
        return Decimal(text)
    except InvalidOperation:
        return None


def _br_date(value: Any) -> date | None:
    """A date as B3 writes it: ``15/04/2024``."""
    if not isinstance(value, str):
        return None
    parts = value.strip().split("/")
    if len(parts) != 3:
        return None
    try:
        return date(int(parts[2]), int(parts[1]), int(parts[0]))
    except ValueError:
        return None


# What B3's ``factor`` means, which is not the same thing for all three actions.
# A DESDOBRAMENTO or BONIFICACAO files the **percentage of new shares** — BBAS3's
# 1:2 split is 100, SAPR4's 1:3 is 200, a 10% bonus is 10 — while a GRUPAMENTO
# files the multiplier outright: HAPV3's 1:15 is 0.0667, MGLU3's 1:10 is 0.10.
# Reading one as the other turns a 10% bonus into a tenfold one.
_EXCHANGE_RATIO: dict[str, Callable[[Decimal], Decimal]] = {
    "DESDOBRAMENTO": lambda factor: 1 + factor / 100,
    "BONIFICACAO": lambda factor: 1 + factor / 100,
    "GRUPAMENTO": lambda factor: factor,
}


def _sum(*counts: Decimal | None) -> Decimal | None:
    """The class counts added up — the stand-in when the filer's total is unusable.

    BBAS3's 2023 FRE files its 5.73 bn ordinary shares and then writes ``0`` in the
    total column (#39). Dropping the whole filing over that one blank column sent
    the year back to 2022's 2.87 bn — pricing the company at half its size, right
    across the 2:1 bonus. The classes are the filing's own numbers, so adding them
    is a reading of what was filed, not a repair of it.
    """
    present = [count for count in counts if count is not None]
    return sum(present, Decimal(0)) if present else None


def _rank(payload: Mapping[str, Any]) -> tuple[int, str]:
    """How one filed capital row beats another: newest amendment, newest approval.

    The approval date is an ISO ``YYYY-MM-DD`` string, so it sorts as filed; a row
    that carries none (any document mirrored before #86) ranks below every dated
    one rather than competing on ingestion order.
    """
    version = payload.get("version")
    approval = payload.get("approval_date")
    return (
        version if isinstance(version, int) else 0,
        approval if isinstance(approval, str) else "",
    )


def _year_of(reference_date: Any) -> int | None:
    """Year from an ISO ``YYYY-MM-DD`` reference date, or None if unparseable."""
    if not isinstance(reference_date, str) or len(reference_date) < 4:
        return None
    try:
        return int(reference_date[:4])
    except ValueError:
        return None


def _served_year[Filed](
    by_year: dict[int, Filed], ticker: str, year: int, what: str
) -> int | None:
    """The filed year that stands for ``year``, or the nearest earlier one on file.

    Say so when it is not the year asked for: a year priced on an adjacent year's
    shares is an approximation, and a silent approximation is indistinguishable from
    a fact (#39). VALE3 has no 2023/2024 FRE in the mirror.
    """
    candidates = [filed for filed in by_year if filed <= year]
    if not candidates:
        return None
    served = max(candidates)
    if served != year:
        logger.info(
            "No %d %s filing for %s; using the %d one", year, what, ticker, served
        )
    return served


def _scaled(counts: ShareCounts, factor: Decimal) -> ShareCounts:
    """``counts`` restated onto the current share base (ADR 0027)."""
    if factor == 1:
        return counts

    def apply(count: Decimal | None) -> Decimal | None:
        return None if count is None else count * factor

    return ShareCounts(
        common=apply(counts.common),
        preferred=apply(counts.preferred),
        total=apply(counts.total),
    )


def _no_unit_composition(_ticker: str) -> int | None:
    """The default resolver: no ticker is a unit, so the filed total stands."""
    return None


class MongoSharesReader:
    """Serves the outstanding share counts per fiscal year from the raw mirror."""

    def __init__(
        self,
        collection: RawCollection,
        *,
        registrant_resolver: RegistrantResolver = no_registrant,
        base_changes: BaseChangeReader | None = None,
        unit_composition_resolver: Callable[[str], int | None] = _no_unit_composition,
        unit_resolver: UnitResolver = no_units,
    ) -> None:
        self._collection = collection
        self._registrant = registrant_resolver
        # Only wired where the price arrives as traded: it dates the chain off
        # the very series that will be divided by it (ADR 0035). ``None`` leaves
        # the timeline exactly as ADRs 0033/0034 built it.
        self._base_changes = base_changes
        # The FCA-derived bundle ratio (``CompanyIdentity.shares_per_unit``,
        # #212) — curated for no ticker, injected by the CLI like every other
        # registry-backed resolver.
        self._unit_composition = unit_composition_resolver
        self._is_unit = unit_resolver

    async def outstanding(self, ticker: str, year: int) -> Decimal | None:
        filed = await self.counts(ticker, year)
        if filed is None or filed.total is None:
            return None
        per_unit = self._unit_composition(ticker)
        if per_unit is not None:
            # A unit bundles ``per_unit`` underlying shares (1 ON + 2 PN), so the
            # per-*unit* LPA/VPA divide by the number of units — the earnings and
            # book value that pair with the unit's own quoted price (#38).
            return filed.total / per_unit
        if self._is_unit(ticker):
            logger.warning(
                "No readable FCA unit composition for %s; per-unit shares are null",
                ticker,
            )
            return None
        return filed.total

    def outstanding_null_reason(self, ticker: str, year: int) -> NullReason | None:
        """Name an unreadable unit denominator separately from a missing filing."""
        if self._is_unit(ticker) and self._unit_composition(ticker) is None:
            return NullReason.MISSING_UNIT_COMPOSITION
        return None

    async def counts(self, ticker: str, year: int) -> ShareCounts | None:
        """The issued classes net of treasury, restated onto the current base.

        When the treasury composition cannot be read — no filing for the year, or a
        scale that will not reconcile against the FRE (ADR 0017) — the issued count
        is served as it stands and the approximation is logged. It over-counts by
        the buyback (a few percent), where a treasury figure subtracted at a guessed
        scale could be off by a thousand.

        The counts served for a year that predates a split/bonus/grupamento are
        multiplied onto the **current** share base (ADR 0027): the per-share series
        stays continuous, and the count pairs with the price it multiplies — the
        exchange's as-traded close is divided by exactly the same moves (ADR 0033),
        and a mismatch is worth the whole action: an as-filed pre-bonus count
        against an adjusted price undercounted BBAS3's pre-2023 caps by the bonus.
        The as-filed reading stays derivable: the mirror keeps every filing, and the
        factor is recomputed from it on every read, never stored.
        """
        by_year = await self._by_year(ticker)
        served = _served_year(by_year, ticker, year, "capital")
        if served is None:
            return None
        issued = by_year[served]
        net = outstanding_counts(issued, await self._composition(ticker, year))
        if net is None:
            logger.info(
                "No readable treasury composition for %s %d; "
                "serving the issued count, which includes any treasury shares",
                ticker,
                year,
            )
            net = issued
        factor = await self._factor(ticker, by_year, served)
        if factor != 1:
            logger.info(
                "Restating %s %d share counts onto the current base (x%s, ADR 0027)",
                ticker,
                served,
                factor,
            )
        return _scaled(net, factor)

    async def _exchange_actions(self, ticker: str) -> tuple[ExchangeAction, ...]:
        """The corporate actions B3 publishes, deduplicated across ISINs.

        B3 lists one row per event **per asset code**, so BBAS3's 2024 split
        arrives three times — same date, same factor, three ISINs. They are one
        event, and counting them three times would cube it.

        Only the three actions that restate the whole share base are read. B3
        files nine labels across the exchange, and the other six carry a factor
        just like these do — measured over 907 rows for 217 companies:

            BONIFICACAO 298 · GRUPAMENTO 263 · DESDOBRAMENTO 196   (restatements)
            CIS RED CAP 97 · RESG TOTAL RV 31 · INCORPORACAO 13 ·
            REST CAP ACOES 6 · CIS RED CAP QTD 2 · REST CAP C/ RED 1

        A spin-off (``CIS RED CAP``) hands shareholders stock in a *different*
        company; an ``INCORPORACAO`` merges one. Neither multiplies the base
        being restated, so an unmapped label contributes nothing rather than
        being guessed at.
        """
        cursor = self._collection.find(
            mirror_filter(
                ticker,
                self._registrant,
                module=CAPITAL_EVENT_B3_MODULE,
            )
        ).sort("fetched_at", 1)
        seen: dict[tuple[str, str, str], ExchangeAction] = {}
        async for document in cursor:
            payload = document.get("payload")
            if not isinstance(payload, Mapping):
                continue
            kind = _upper(payload.get("event_type"))
            factor = _br_decimal(payload.get("factor"))
            approval = _br_date(payload.get("approval_date"))
            last_prior = _br_date(payload.get("last_date_prior"))
            if kind not in _EXCHANGE_RATIO or factor is None:
                continue
            if approval is None or last_prior is None:
                continue
            ratio = _EXCHANGE_RATIO[kind](factor)
            if ratio <= 0:
                continue
            key = (approval.isoformat(), kind, str(factor))
            seen[key] = ExchangeAction(
                # ``lastDatePrior`` is the last session on the *old* base, so the
                # new one starts the day after — and a step applies to everything
                # quoted strictly before its ``effective``.
                effective=last_prior + timedelta(days=1),
                approval_date=approval.isoformat(),
                ratio=ratio,
            )
        return tuple(seen.values())

    async def restatement_timeline(self, ticker: str) -> tuple[RestatementStep, ...]:
        """The dated share-base moves ``counts`` restates by — see the port.

        Read by the price side, which must divide by exactly these because the
        source publishes the price **as traded** (ADR 0032).
        Same inputs and same chain as ``counts``, deliberately: a price adjusted by
        a *better* factor than the count it multiplies would break the very
        invariance that keeps the cap right. What the price adds is the date each
        move happened on, which a yearly count series has no use for (ADR 0033).
        """
        by_year = await self._by_year(ticker)
        return restatement_timeline(*await self._restatement_inputs(ticker, by_year))

    async def _session_changes(
        self, ticker: str, by_year: Mapping[int, ShareCounts]
    ) -> tuple[BaseChange, ...]:
        """The base changes B3's tape carries over the years the counts cover.

        Bounded by the filed years and one beyond them, because an action is
        reported by the *following* year's FRE: BBAS3's April 2024 split is first
        counted by the 2023 form, so a chain that stopped at 2023 would never be
        offered the session it happened on.
        """
        if self._base_changes is None or not by_year:
            return ()
        years = range(min(by_year), max(by_year) + 2)
        return tuple(await self._base_changes.base_changes(ticker, tuple(years)))

    async def _factor(
        self, ticker: str, by_year: dict[int, ShareCounts], served: int
    ) -> Decimal:
        factors = restatement_factors(*await self._restatement_inputs(ticker, by_year))
        return factors.get(served, Decimal(1))

    async def _restatement_inputs(
        self, ticker: str, by_year: dict[int, ShareCounts]
    ) -> tuple[
        dict[int, Decimal],
        list[Decimal],
        tuple[CorporateAction, ...],
        tuple[BaseChange, ...],
        tuple[ExchangeAction, ...],
    ]:
        """The four readings the restatement takes, in the order both entries use.

        Both of B3's records are among them because they now bear on the *ratio*
        and not only on the date: a gap holding an action and an issuance leaves
        the counts dirty, and it takes a second witness to say an action was in
        there — the feed where its factors reconcile with the move (ADR 0038),
        the tape where they do not (ADR 0037).
        """
        return (
            {y: c.total for y, c in by_year.items() if c.total is not None},
            await self._composition_units_series(ticker, by_year),
            await self._declared_actions(ticker),
            await self._session_changes(ticker, by_year),
            await self._exchange_actions(ticker),
        )

    async def _declared_actions(self, ticker: str) -> tuple[CorporateAction, ...]:
        """The corporate actions the company declared to CVM, deduplicated.

        Every later FRE restates the whole history, so the mirror holds the same
        event once per year it was filed in (ADR 0016). An event is identified by
        its approval date and type; the highest ``version`` of it wins, the same
        rule the capital rows use.
        """
        cursor = self._collection.find(
            mirror_filter(ticker, self._registrant, module=CAPITAL_EVENT_MODULE)
        ).sort("fetched_at", 1)
        best: dict[tuple[str, str], tuple[int, CorporateAction]] = {}
        async for document in cursor:
            payload = document.get("payload")
            if not isinstance(payload, Mapping):
                continue
            approval = payload.get("approval_date")
            kind = payload.get("event_type")
            before = _dec(payload.get("total_before"))
            after = _dec(payload.get("total_after"))
            if not isinstance(approval, str) or not isinstance(kind, str):
                continue
            if before is None or after is None:
                continue
            version = payload.get("version")
            rank = version if isinstance(version, int) else 0
            key = (approval, kind)
            if key in best and rank < best[key][0]:
                continue
            best[key] = (
                rank,
                CorporateAction(
                    approval_date=approval,
                    kind=kind,
                    total_before=before,
                    total_after=after,
                ),
            )
        return tuple(action for _rank, action in best.values())

    async def _composition(self, ticker: str, year: int) -> CapitalComposition | None:
        compositions = await self._compositions(ticker)
        served = _served_year(compositions, ticker, year, "composition")
        return None if served is None else compositions[served]

    async def _by_year(self, ticker: str) -> dict[int, ShareCounts]:
        """The capital composition that supersedes the rest, per year.

        Two filed facts order the candidates, in this order:

        * ``version`` — the mirror holds every FRE amendment (ADR 0016), so
          ingestion time is not enough: the highest amendment stands, whenever it
          happened to be ingested.
        * ``approval_date`` — *within* an amendment, the member is a history of
          capital events, and several of its rows are paid-in. SANEPAR's 2021 FRE
          files the 2020 split (1.51 bn shares) next to two 2016 approvals (503 M);
          the company's capital is the one most recently approved. Picking by
          cursor order instead served SANEPAR's 2016 capital, pricing the company
          at a third of its size (#86).

        ``fetched_at`` only breaks a tie between two copies of the same row. An
        undated row (mirrored before #86) sorts below every dated one, so a stale
        copy in the append-only mirror can never win.
        """
        cursor = self._collection.find(
            mirror_filter(ticker, self._registrant, module=CAPITAL_MODULE)
        ).sort("fetched_at", 1)
        by_year: dict[int, ShareCounts] = {}
        best: dict[int, tuple[int, str]] = {}
        async for document in cursor:
            payload = document.get("payload")
            if not isinstance(payload, Mapping):
                continue
            year = _year_of(payload.get("reference_date"))
            if year is None:
                continue
            rank = _rank(payload)
            if year in best and rank < best[year]:
                continue
            common = _positive(payload.get("common_shares"))
            preferred = _positive(payload.get("preferred_shares"))
            total = _positive(payload.get("total_shares")) or _sum(common, preferred)
            if total is None:
                continue
            best[year] = rank
            by_year[year] = ShareCounts(common=common, preferred=preferred, total=total)
        return by_year

    async def _compositions(self, ticker: str) -> dict[int, CapitalComposition]:
        """The statements' capital composition — the treasury side — per year.

        The mirror holds one per filed period (ADR 0016), so a year has the DFP's
        year-end row *and* the three ITR quarters. The **latest reference date**
        within the year wins: for a closed year that is the DFP's 31-Dec row, and
        for the current year it is the freshest quarter — which is what the live TTM
        wants. ``version`` breaks a tie between two filings of the same period.
        """
        cursor = self._collection.find(
            mirror_filter(ticker, self._registrant, module=TREASURY_MODULE)
        ).sort("fetched_at", 1)
        by_year: dict[int, CapitalComposition] = {}
        best: dict[int, tuple[str, int]] = {}
        async for document in cursor:
            payload = document.get("payload")
            if not isinstance(payload, Mapping):
                continue
            reference_date = payload.get("reference_date")
            year = _year_of(reference_date)
            if year is None or not isinstance(reference_date, str):
                continue
            version = payload.get("version")
            rank = (reference_date, version if isinstance(version, int) else 0)
            if year in best and rank < best[year]:
                continue
            best[year] = rank
            by_year[year] = CapitalComposition(
                issued_total=_positive(payload.get("total_shares")),
                # Zero is a fact here, not an absence: it is how a company with no
                # buyback files the row. Only a missing key reads as unknown.
                treasury_common=_dec(payload.get("treasury_common_shares")),
                treasury_preferred=_dec(payload.get("treasury_preferred_shares")),
                treasury_total=_dec(payload.get("treasury_total_shares")),
            )
        return by_year

    async def _composition_units_series(
        self, ticker: str, issued_by_year: dict[int, ShareCounts]
    ) -> list[Decimal]:
        """The composition's issued totals per filed period, units-scale rows only.

        Feeds the split detection of ADR 0028. The **full quarter-by-quarter**
        series, not the latest-per-year one: a split and a same-year cancellation
        only separate at quarter granularity (VIVT3's 2:1 shows between 2025-Q1 and
        2025-Q2, where the FRE year already fuses them). Each period's scale is
        reconciled against that year's FRE total (ADR 0017) and a thousands-scale
        row is dropped — its rounding forbids the exact-to-the-share ratio the
        detection depends on (LREN3's buyback would round to a false 19/20).
        """
        cursor = self._collection.find(
            mirror_filter(ticker, self._registrant, module=TREASURY_MODULE)
        ).sort("fetched_at", 1)
        best: dict[str, tuple[int, Decimal]] = {}  # reference_date -> (version, total)
        async for document in cursor:
            payload = document.get("payload")
            if not isinstance(payload, Mapping):
                continue
            reference_date = payload.get("reference_date")
            total = _positive(payload.get("total_shares"))
            if not isinstance(reference_date, str) or total is None:
                continue
            version = payload.get("version")
            rank = version if isinstance(version, int) else 0
            if reference_date in best and rank < best[reference_date][0]:
                continue
            best[reference_date] = (rank, total)
        series: list[Decimal] = []
        for reference_date in sorted(best):
            year = _year_of(reference_date)
            filed = issued_by_year.get(year) if year is not None else None
            if filed is None or filed.total is None:
                continue
            total = best[reference_date][1]
            if filed_scale(filed.total, total) == Decimal(1):  # units, not thousands
                series.append(total)
        return series
