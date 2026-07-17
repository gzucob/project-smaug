"""Regenerate ``tests/fixtures/analysis_inputs.json`` — the fidelity gate's inputs.

The gate (#44) compares our indicators against the reference platform's published
values. The platform's side is a hand-captured fixture; *our* side has to be
computed, and the test that computes it runs in CI, where there is no Mongo and no
Postgres. So the inputs the calculator needs — one closed year's standardized
financials, plus the market data for that year — are exported here, once, and
committed. The test then runs the real ``compute()`` over them.

That is the point: the fixture pins **the calculator**, not a snapshot of its
output. A change to `calculator.py` that breaks fidelity fails the test; a change
to the *mapping* does not, and is covered by ``smaug doctor`` and the reader's own
tests instead.

Covers the nine plus the sector representatives (one liquid name per B3 setor the
nine miss). The representatives are resolved through the CVM FCA registry — their
sector (the regime hint) and their share classes (for the cap) are not curated,
exactly as the ``analyze`` CLI resolves them (ADR 0023/0025).

Run against a live mirror (Mongo up, Yahoo reachable):

    uv run python scripts/export_reference_inputs.py
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import Callable
from dataclasses import fields
from decimal import Decimal
from pathlib import Path
from typing import Any

import httpx

from smaug.analysis.domain.financials import StandardizedFinancials
from smaug.analysis.domain.market_cap import capitalize
from smaug.analysis.infrastructure.brapi_price import BrapiPriceProvider
from smaug.analysis.infrastructure.fallback_price import FallbackPriceHistory
from smaug.analysis.infrastructure.mongo_capital import MongoSharesReader
from smaug.analysis.infrastructure.mongo_fundamentals import MongoFundamentalsReader
from smaug.analysis.infrastructure.yahoo_price import YahooPriceHistory
from smaug.portfolio.domain.company import CompanyIdentity
from smaug.portfolio.domain.sectors import (
    PORTFOLIO,
    Sector,
    portfolio_tickers,
    sector_from_cvm,
)
from smaug.portfolio.domain.share_classes import ShareClass, listed_classes
from smaug.portfolio.infrastructure.cvm_registry import CvmCompanyRegistry
from smaug.shared.config import get_settings
from smaug.shared.db import init_database

# The closed years the platform publishes a full indicator column for.
YEARS = (2024, 2025)

# Sector representatives — one liquid name per B3 setor econômico the nine miss.
REPRESENTATIVES = ("ABEV3", "LREN3", "HAPV3", "TOTS3", "VIVT3")

OUTPUT = Path(__file__).resolve().parent.parent / "tests/fixtures/analysis_inputs.json"


def _str(value: Any) -> Any:
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, frozenset):
        return sorted(value)
    return None if value is None else str(value)


def _sector_resolver(
    identities: dict[str, CompanyIdentity],
) -> Callable[[str], Sector]:
    def resolve(ticker: str) -> Sector:
        if ticker in PORTFOLIO:
            return PORTFOLIO[ticker]
        return sector_from_cvm(identities[ticker].cvm_sector)

    return resolve


def _classes_resolver(
    identities: dict[str, CompanyIdentity],
) -> Callable[[str], tuple[ShareClass, ...]]:
    def resolve(ticker: str) -> tuple[ShareClass, ...]:
        curated = listed_classes(ticker)
        if curated:
            return curated
        identity = identities.get(ticker)
        return identity.share_classes if identity is not None else ()

    return resolve


async def main() -> None:
    settings = get_settings()
    mongo = await init_database(settings)
    collection = mongo[settings.mongo_db]["raw_ingestions"]
    shares = MongoSharesReader(collection)
    export: dict[str, Any] = {}

    async with httpx.AsyncClient(timeout=30.0) as http:
        registry = CvmCompanyRegistry(
            http, year=settings.cvm_year, cache_dir=settings.cvm_cache_dir
        )
        identities = await registry.resolve_all(REPRESENTATIVES)
        sector_of = _sector_resolver(identities)
        classes_of = _classes_resolver(identities)
        reader = MongoFundamentalsReader(collection, sector_resolver=sector_of)
        history = FallbackPriceHistory(
            [
                YahooPriceHistory(settings.yahoo_base_url, http),
                BrapiPriceProvider(
                    settings.brapi_base_url,
                    settings.brapi_token.get_secret_value(),
                    http,
                ),
            ]
        )
        tickers = (*portfolio_tickers(), *REPRESENTATIVES)
        for ticker in tickers:
            annuals = {a.reference_date.year: a for a in await reader.annuals(ticker)}
            for year in YEARS:
                annual = annuals.get(year)
                if annual is None:
                    continue
                counts = await shares.counts(ticker, year)
                # The nominal average is what a valuation multiple divides by (ADR
                # 0018) — the same price the use case sums the cap over.
                by_class = {
                    share_class.symbol: (
                        await history.year_prices(share_class.symbol, year)
                    ).nominal_avg
                    for share_class in classes_of(ticker)
                }
                cap, _ = capitalize(classes_of(ticker), counts, by_class)
                own = await history.year_prices(ticker, year)
                export.setdefault(ticker, {})[str(year)] = {
                    "financials": {
                        field.name: _str(getattr(annual, field.name))
                        for field in fields(StandardizedFinancials)
                    },
                    "market": {
                        "price": _str(own.nominal_avg),
                        "market_cap": _str(cap),
                        "shares": _str(await shares.outstanding(ticker, year)),
                    },
                }

    await mongo.close()
    OUTPUT.write_text(json.dumps(export, indent=2, ensure_ascii=False) + "\n", "utf-8")
    print(f"wrote {OUTPUT} ({len(export)} tickers)")


if __name__ == "__main__":
    asyncio.run(main())
