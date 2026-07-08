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

# DRE bottom line, in priority order (name varies by sector).
_NET_INCOME_NAMES = (
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


def _sum(*values: Decimal | None) -> Decimal | None:
    total = Decimal(0)
    present = False
    for value in values:
        if value is not None:
            total += value
            present = True
    return total if present else None


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
    bpa, bpa_s = _accounts(by_module, "BPA"), _scale(by_module, "BPA")
    bpp, bpp_s = _accounts(by_module, "BPP"), _scale(by_module, "BPP")
    dre, dre_s = _accounts(by_module, "DRE"), _scale(by_module, "DRE")
    dfc, dfc_s = _accounts(by_module, "DFC"), _scale(by_module, "DFC")

    net_income: Decimal | None = None
    for name in _NET_INCOME_NAMES:
        net_income = _by_name(dre, name)
        if net_income is not None:
            break

    total_assets = _mul(_by_code(bpa, "1"), bpa_s)
    equity = _mul(_by_name(bpp, "patrimonio liquido"), bpp_s)
    net_income = _mul(net_income, dre_s)
    revenue = _mul(_by_code(dre, "3.01"), dre_s)

    if sector.is_financial:
        return StandardizedFinancials(
            reference_date=reference_date,
            sector=sector,
            total_assets=total_assets,
            equity=equity,
            net_income=net_income,
            revenue=revenue,
        )

    ebit = _mul(_by_code(dre, "3.05"), dre_s)  # before financial result/taxes
    dep_amort = _mul(_by_name(dfc, "depreciacao"), dfc_s)  # cash-flow add-back
    ebitda = (
        _sum(ebit, dep_amort) if ebit is not None and dep_amort is not None else None
    )
    return StandardizedFinancials(
        reference_date=reference_date,
        sector=sector,
        total_assets=total_assets,
        equity=equity,
        net_income=net_income,
        revenue=revenue,
        gross_profit=_mul(_by_code(dre, "3.03"), dre_s),
        ebit=ebit,
        ebitda=ebitda,
        dep_amort=dep_amort,
        cash=_mul(_by_code(bpa, "1.01.01"), bpa_s),
        current_assets=_mul(_by_code(bpa, "1.01"), bpa_s),
        current_liabilities=_mul(_by_code(bpp, "2.01"), bpp_s),
        total_debt=_mul(
            _sum(_by_code(bpp, "2.01.04"), _by_code(bpp, "2.02.01")), bpp_s
        ),
    )


class MongoFundamentalsReader:
    """Reads the CVM mirror and yields standardized financials (oldest→newest)."""

    def __init__(self, collection: RawCollection) -> None:
        self._collection = collection

    async def history(self, ticker: str) -> list[StandardizedFinancials]:
        cursor = self._collection.find({"source": "cvm", "ticker": ticker})
        docs: list[Mapping[str, Any]] = await cursor.to_list(None)
        sector = sector_of(ticker)

        by_period: dict[str, dict[str, Any]] = {}
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

        return [
            standardize(modules, sector, date.fromisoformat(ref))
            for ref, modules in sorted(by_period.items())
        ]
