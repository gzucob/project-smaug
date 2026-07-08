"""Maps the raw CVM mirror (Mongo) into standardized financials.

This is the derivation bridge: it reads the append-only ``raw_ingestions`` docs
that the CVM ingestion stored (source="cvm"), groups them by reference period,
and pulls the specific accounts each indicator needs — by CVM code where the
code is stable across sectors, and by (accent-folded) name where the code
differs (equity is 2.03 for a normal company but 2.07 for a bank).

Account codes were verified against the real 2024 ITR. The structure differs by
sector, so the mapping is sector-aware: for banks/insurers only the lines that
make sense (total assets, equity, net income, revenue) are pulled; the rest stay
``None`` and the calculator skips the corresponding ratios.
"""

from __future__ import annotations

import unicodedata
from collections.abc import Mapping, Sequence
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from typing import Any, Protocol

from smaug.analysis.domain.financials import StandardizedFinancials
from smaug.portfolio.domain.sectors import Sector, sector_of

_STATEMENTS = ("BPA", "BPP", "DRE", "DFC")

# Closed-year (historical) view: keep only annual periods. In Brazil the annual
# DFP closes on 31-Dec, while the ITRs are Q1–Q3 (never December), so the month
# alone distinguishes a closed year without depending on pycvm's document enum.
_CLOSED_YEAR_MONTH = 12

# Consolidated DRE bottom line, in priority order (label varies by sector). Used
# only as a fallback when the controllers' line is absent — see ``_net_income``.
_NET_INCOME_TOTAL_NAMES = (
    "lucro ou prejuizo liquido consolidado do periodo",
    "lucro/prejuizo consolidado do periodo",
    "resultado liquido das operacoes continuadas",
    "lucro ou prejuizo das operacoes continuadas",
)

Accounts = Sequence[Mapping[str, Any]]


class RawCollection(Protocol):
    """Minimal read surface over the ``raw_ingestions`` collection."""

    def find(self, filter: Mapping[str, Any], /) -> Any: ...


def _fold(text: str) -> str:
    decomposed = unicodedata.normalize("NFKD", text)
    return "".join(c for c in decomposed if not unicodedata.combining(c)).casefold()


def _dec(value: Any) -> Decimal | None:
    if value is None:
        return None
    try:
        return Decimal(str(value))
    except (InvalidOperation, ValueError):
        return None


def _by_code(accounts: Accounts, code: str) -> Decimal | None:
    for account in accounts:
        if str(account.get("code")) == code:
            return _dec(account.get("quantity"))
    return None


def _by_name(accounts: Accounts, needle: str) -> Decimal | None:
    folded = _fold(needle)
    for account in accounts:
        if folded in _fold(str(account.get("name", ""))):
            return _dec(account.get("quantity"))
    return None


def _controllers_share(
    accounts: Accounts,
    *,
    controllers: str,
    total: Decimal | None,
    minority: str,
) -> Decimal | None:
    """The controlling shareholders' slice of a consolidated figure.

    Platforms report the controllers' equity/earnings, not the consolidated
    total that still carries the minority interest. The split is exposed in two
    shapes: an explicit "attributed to the controller" line (banks), or a
    consolidated total plus a "non-controlling" sub-line (most companies). Prefer
    the explicit line; else ``total − minority``; else the total unchanged (no
    split filed → the total already is the controllers' figure).
    """
    explicit = _by_name(accounts, controllers)
    if explicit is not None:
        return explicit
    minority_value = _by_name(accounts, minority)
    if total is not None and minority_value is not None:
        return total - minority_value
    return total


def _net_income(dre: Accounts) -> Decimal | None:
    """Net income attributable to the controlling shareholders (DRE)."""
    total: Decimal | None = None
    for name in _NET_INCOME_TOTAL_NAMES:
        total = _by_name(dre, name)
        if total is not None:
            break
    return _controllers_share(
        dre,
        controllers="socios da empresa controladora",
        total=total,
        minority="socios nao controladores",
    )


def _equity(bpp: Accounts) -> Decimal | None:
    """Equity attributable to the controlling shareholders (BPP)."""
    return _controllers_share(
        bpp,
        controllers="atribuido ao controlador",
        total=_by_name(bpp, "patrimonio liquido"),
        minority="nao controladores",
    )


def _dividends_paid(dfc: Accounts) -> Decimal | None:
    """Dividends + interest-on-equity (JCP) paid to controlling shareholders.

    Financing-section cash outflows whose label mentions a dividend or JCP
    (``capital proprio``) and "pago", excluding the non-controlling line.
    Returned positive (the DFC records them as negative outflows); ``None`` when
    no such line exists, so DY degrades to null rather than zero.
    """
    total = Decimal(0)
    found = False
    for account in dfc:
        name = _fold(str(account.get("name", "")))
        if "pag" not in name or "nao control" in name:
            continue
        if "dividendo" not in name and "capital proprio" not in name:
            continue
        value = _dec(account.get("quantity"))
        if value is not None:
            total += abs(value)
            found = True
    return total if found else None


def _sum(*values: Decimal | None) -> Decimal | None:
    total = Decimal(0)
    present = False
    for value in values:
        if value is not None:
            total += value
            present = True
    return total if present else None


def _iso_date(raw: Any) -> date | None:
    if not isinstance(raw, str):
        return None
    try:
        return date.fromisoformat(raw)
    except ValueError:
        return None


def _period_start(by_module: Mapping[str, Any], module: str) -> date | None:
    """Start of a statement's flow period (its ``period_start_date``)."""
    payload = by_module.get(module)
    if isinstance(payload, Mapping):
        return _iso_date(payload.get("period_start_date"))
    return None


def _accounts(by_module: Mapping[str, Any], module: str) -> Accounts:
    payload = by_module.get(module)
    if not isinstance(payload, Mapping):
        return []
    accounts = payload.get("accounts")
    return accounts if isinstance(accounts, list) else []


def _scale(by_module: Mapping[str, Any], module: str) -> Decimal:
    """CVM figures are reported in ``currency_size`` units (usually thousands).

    Scaling to absolute reais here is what keeps the market multiples honest —
    brapi's market cap is in reais, so mixing the two unscaled inflates P/E,
    P/B and EV/EBITDA by ~1000x.
    """
    payload = by_module.get(module)
    if isinstance(payload, Mapping):
        size = payload.get("currency_size")
        if isinstance(size, int) and size > 0:
            return Decimal(size)
    return Decimal(1)


def _mul(value: Decimal | None, scale: Decimal) -> Decimal | None:
    return None if value is None else value * scale


def standardize(
    by_module: Mapping[str, Any], sector: Sector, reference_date: date
) -> StandardizedFinancials:
    """Build one period's ``StandardizedFinancials`` from its CVM statements."""
    period_start = _period_start(by_module, "DRE")
    dfc_period_start = _period_start(by_module, "DFC")
    bpa, bpa_s = _accounts(by_module, "BPA"), _scale(by_module, "BPA")
    bpp, bpp_s = _accounts(by_module, "BPP"), _scale(by_module, "BPP")
    dre, dre_s = _accounts(by_module, "DRE"), _scale(by_module, "DRE")
    dfc, dfc_s = _accounts(by_module, "DFC"), _scale(by_module, "DFC")

    total_assets = _mul(_by_code(bpa, "1"), bpa_s)
    equity = _mul(_equity(bpp), bpp_s)
    net_income = _mul(_net_income(dre), dre_s)
    revenue = _mul(_by_code(dre, "3.01"), dre_s)
    dividends_paid = _mul(_dividends_paid(dfc), dfc_s)

    if sector.is_financial:
        return StandardizedFinancials(
            reference_date=reference_date,
            sector=sector,
            period_start=period_start,
            dfc_period_start=dfc_period_start,
            total_assets=total_assets,
            equity=equity,
            net_income=net_income,
            revenue=revenue,
            dividends_paid=dividends_paid,
        )

    ebit = _mul(_by_code(dre, "3.05"), dre_s)  # before financial result/taxes
    dep_amort = _mul(_by_name(dfc, "depreciacao"), dfc_s)  # cash-flow add-back
    ebitda = (
        _sum(ebit, dep_amort) if ebit is not None and dep_amort is not None else None
    )
    # Cash for net debt = cash & equivalents (1.01.01) + short-term financial
    # investments (1.01.02), matching how the platforms measure liquidity.
    cash = _mul(_sum(_by_code(bpa, "1.01.01"), _by_code(bpa, "1.01.02")), bpa_s)
    return StandardizedFinancials(
        reference_date=reference_date,
        sector=sector,
        period_start=period_start,
        dfc_period_start=dfc_period_start,
        total_assets=total_assets,
        equity=equity,
        net_income=net_income,
        revenue=revenue,
        gross_profit=_mul(_by_code(dre, "3.03"), dre_s),
        ebit=ebit,
        ebitda=ebitda,
        dep_amort=dep_amort,
        cash=cash,
        current_assets=_mul(_by_code(bpa, "1.01"), bpa_s),
        current_liabilities=_mul(_by_code(bpp, "2.01"), bpp_s),
        total_debt=_mul(
            _sum(_by_code(bpp, "2.01.04"), _by_code(bpp, "2.02.01")), bpp_s
        ),
        dividends_paid=dividends_paid,
    )


def _is_annual(doc_type: str | None, financials: StandardizedFinancials) -> bool:
    """A closed year: the DFP document, or (lacking the tag) a December period."""
    if doc_type is not None:
        return doc_type.upper() == "DFP"
    return financials.reference_date.month == _CLOSED_YEAR_MONTH


class MongoFundamentalsReader:
    """Reads the CVM mirror: ITR quarters (history) and the annual DFP (annual)."""

    def __init__(self, collection: RawCollection) -> None:
        self._collection = collection

    async def history(self, ticker: str) -> list[StandardizedFinancials]:
        """ITR quarterly periods (oldest→newest) — the raw material for the TTM."""
        return [f for dt, f in await self._load(ticker) if not _is_annual(dt, f)]

    async def annuals(self, ticker: str) -> list[StandardizedFinancials]:
        """Annual DFPs (closed years), oldest→newest."""
        return [f for dt, f in await self._load(ticker) if _is_annual(dt, f)]

    async def annual(self, ticker: str) -> StandardizedFinancials | None:
        """The most recent annual DFP (closed year), for the Q4 derivation."""
        annuals = await self.annuals(ticker)
        return annuals[-1] if annuals else None

    async def _load(
        self, ticker: str
    ) -> list[tuple[str | None, StandardizedFinancials]]:
        cursor = self._collection.find({"source": "cvm", "ticker": ticker})
        docs: list[Mapping[str, Any]] = await cursor.to_list(None)
        sector = sector_of(ticker)

        by_period: dict[str, dict[str, Any]] = {}
        doc_type: dict[str, str | None] = {}
        latest_fetch: dict[tuple[str, str], datetime] = {}
        for doc in docs:
            payload = doc.get("payload")
            module = doc.get("module")
            fetched = doc.get("fetched_at")
            if (
                not isinstance(payload, Mapping)
                or module not in _STATEMENTS
                or fetched is None
            ):
                continue
            ref = payload.get("reference_date")
            if not isinstance(ref, str):
                continue
            key = (ref, module)
            if key not in latest_fetch or fetched > latest_fetch[key]:
                latest_fetch[key] = fetched
                by_period.setdefault(ref, {})[module] = payload
                tag = payload.get("document_type")
                if isinstance(tag, str):
                    doc_type[ref] = tag

        return [
            (doc_type.get(ref), standardize(modules, sector, date.fromisoformat(ref)))
            for ref, modules in sorted(by_period.items())
        ]
