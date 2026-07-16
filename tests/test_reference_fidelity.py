"""The M1 gate (#44): our indicators against a reference platform's published values.

Correctness of the nine is a test, not a paragraph. The platform's values are a
committed fixture (``reference_platforms.json``); our side is **computed here, by the
real calculator**, from the inputs exported in ``analysis_inputs.json`` — so a change
to `calculator.py` that breaks fidelity fails, rather than being measured against a
snapshot of its own output.

A divergence we accept must be written down, cell by cell, in the fixture's ``except``
block, with the reason. Several of them turn out to be the *platform's* mistake — it
prices a unit's bundle against a per-share earnings figure (#38), and it mixes the
consolidated result into ratios whose per-share sibling uses the controllers' line.
Writing the reason down is what keeps that distinction visible; widening a tolerance
would erase it.
"""

from __future__ import annotations

import json
from dataclasses import fields
from datetime import date
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest

from smaug.analysis.domain.calculator import compute
from smaug.analysis.domain.financials import (
    AccountingRegime,
    MarketData,
    StandardizedFinancials,
)
from smaug.analysis.domain.indicators import Indicators
from smaug.portfolio.domain.sectors import Sector, sector_of
from smaug.shared.errors import UnknownTickerError

_FIXTURES = Path(__file__).parent / "fixtures"
_REFERENCE = json.loads((_FIXTURES / "reference_platforms.json").read_text("utf-8"))
_INPUTS = json.loads((_FIXTURES / "analysis_inputs.json").read_text("utf-8"))

_TOLERANCES: dict[str, float] = {
    name: value
    for name, value in _REFERENCE["tolerances"].items()
    if not name.startswith("_")
}


def _dec(value: Any) -> Decimal | None:
    return None if value is None else Decimal(str(value))


def _financials(raw: dict[str, Any]) -> StandardizedFinancials:
    """Rebuild the exported period — the entity the use case hands the calculator."""
    typed: dict[str, Any] = {}
    for field in fields(StandardizedFinancials):
        value = raw[field.name]
        if value is None:
            typed[field.name] = None
        elif field.name in ("reference_date", "period_start", "dfc_period_start"):
            typed[field.name] = date.fromisoformat(value)
        elif field.name == "sector":
            typed[field.name] = Sector(value)
        elif field.name == "filed_regime":
            typed[field.name] = AccountingRegime(value)
        elif field.name == "unmapped_fields":
            typed[field.name] = frozenset(value)
        else:
            typed[field.name] = Decimal(value)
    return StandardizedFinancials(**typed)


def _ours(ticker: str, year: str) -> Indicators:
    exported = _INPUTS[ticker][year]
    market = MarketData(
        price=_dec(exported["market"]["price"]),
        market_cap=_dec(exported["market"]["market_cap"]),
        shares=_dec(exported["market"]["shares"]),
    )
    # ``previous`` is the year-over-year growth base only; no growth indicator is
    # compared here, and the platform publishes none.
    return compute(_financials(exported["financials"]), None, market)


def _cells() -> list[tuple[str, str, str, float]]:
    """Every (ticker, year, indicator, published value) the platform gives us."""
    cells = []
    for ticker, years in _REFERENCE["tickers"].items():
        for year, published in years.items():
            for indicator, value in published.items():
                if indicator == "except" or indicator not in _TOLERANCES:
                    continue
                cells.append((ticker, year, indicator, value))
    return cells


def _reason(ticker: str, year: str, indicator: str) -> str | None:
    published = _REFERENCE["tickers"][ticker][year]
    return published.get("except", {}).get(indicator)


@pytest.mark.parametrize(("ticker", "year", "indicator", "published"), _cells())
def test_our_indicator_matches_the_reference_platform(
    ticker: str, year: str, indicator: str, published: float
) -> None:
    reason = _reason(ticker, year, indicator)
    ours = getattr(_ours(ticker, year), indicator)

    if reason is not None:
        # A recorded divergence still has to *be* one: if the cell now agrees, the
        # exception is stale and the fixture is telling a story about the past.
        if ours is None:
            return
        drift = abs(float(ours) - published) / abs(published)
        assert drift > _TOLERANCES[indicator], (
            f"{ticker} {year} {indicator} now agrees with the platform "
            f"({ours} vs {published}); drop its `except` entry: {reason}"
        )
        return

    assert ours is not None, (
        f"{ticker} {year} {indicator} is null; the platform publishes {published}"
    )
    drift = abs(float(ours) - published) / abs(published)
    assert drift <= _TOLERANCES[indicator], (
        f"{ticker} {year} {indicator}: ours {float(ours):.4f} vs "
        f"{_REFERENCE['platform']} {published} — {drift:.1%} apart, tolerance "
        f"{_TOLERANCES[indicator]:.0%}"
    )


def test_every_recorded_divergence_carries_its_reason() -> None:
    # The fixture's whole point: an accepted divergence is a written decision, never a
    # tolerance quietly widened until the number fits.
    for ticker, years in _REFERENCE["tickers"].items():
        for year, published in years.items():
            for indicator, reason in published.get("except", {}).items():
                assert indicator in _TOLERANCES, (
                    f"{ticker} {year}: `except` names {indicator}, not compared"
                )
                assert reason.strip(), f"{ticker} {year} {indicator}: empty reason"


@pytest.mark.parametrize("ticker", sorted(_INPUTS))
def test_no_closed_year_pays_out_more_than_the_company_is_worth(ticker: str) -> None:
    # The invariant that would have caught ADR 0018 five months earlier. Pricing a
    # closed year on the dividend-adjusted series shrank the cap the payout divides
    # by, until PETR4's 2022 dividend yield read 105.9% — the company handing out
    # more than the market said it was worth. A yield can be high (PETR4 2022 is
    # genuinely ~46%); it cannot exceed the whole company.
    for year in _INPUTS[ticker]:
        yield_ = _ours(ticker, year).dividend_yield
        assert yield_ is None or yield_ < 1, (
            f"{ticker} {year}: dividend yield {float(yield_):.1%} — a company cannot "
            f"pay out more than its own market capitalization"
        )


def _is_bank(ticker: str) -> bool:
    # The sector representatives sit outside the static nine-ticker portfolio
    # map; none of them is a bank, so an unknown ticker reads as "not a bank"
    # rather than an error.
    try:
        return sector_of(ticker) is Sector.BANK
    except UnknownTickerError:
        return False


def _roe_tickers() -> list[str]:
    """Tickers whose 2025 ROE the platform publishes and we compare (#45).

    The two banks are left out on purpose — see the test below.
    """
    return sorted(
        ticker
        for ticker, years in _REFERENCE["tickers"].items()
        if "roe" in years.get("2025", {})
        and _reason(ticker, "2025", "roe") is None
        and "2024" in _INPUTS.get(ticker, {})
        and not _is_bank(ticker)
    )


@pytest.mark.parametrize("ticker", _roe_tickers())
def test_roe_divides_by_the_closing_balance_like_the_platforms(ticker: str) -> None:
    # #45: ADR 0003 divides the year's result by the equity at its *close*, not by
    # the average of the opening and closing balances — a choice made without a
    # measurement behind it. This is the measurement.
    #
    # The closing basis lands on the platform's published ROE exactly for four of
    # these and within 0.7% for the rest; the average basis misses by 2.6% to 11.9%
    # (WEGE3 by 11.9% in both years). Whoever is tempted to "fix" this to an average
    # balance has to get past this test first.
    #
    # The two **banks** are excluded, and the exclusion is itself a finding: for
    # BBAS3 and BBDC4 the platform's ROE fits the *average* balance, and its book
    # value per share for them equals the two-year average equity (BBAS3: 185.0 bn /
    # 5.73 bn shares = 32.29 against its published 32.21). That is exactly the
    # anomaly #93 records — the platform's bank equity column, not our basis.
    published = _REFERENCE["tickers"][ticker]["2025"]["roe"]
    exported = _INPUTS[ticker]
    result = Decimal(exported["2025"]["financials"]["net_income"])
    closing = Decimal(exported["2025"]["financials"]["equity"])
    opening = Decimal(exported["2024"]["financials"]["equity"])

    on_closing = float(result / closing)
    on_average = float(result / ((opening + closing) / 2))

    # The platform publishes ROE to two decimals of a percent (a step of 0.0001
    # as a fraction). When the two candidate bases sit closer together than that
    # step, the published cell cannot tell them apart — the same
    # no-information-to-compare reasoning that keeps `roa` out of the gate.
    # HAPV3 2025 is the live case: −0.2938% (closing) vs −0.2925% (average)
    # against a published −0.29%.
    if abs(on_closing - on_average) < 0.0001:
        pytest.skip(
            f"{ticker} 2025: closing ({on_closing:.4%}) and average "
            f"({on_average:.4%}) ROE differ by less than the platform's "
            f"rounding step — the cell cannot discriminate the basis"
        )

    assert abs(on_closing - published) < abs(on_average - published), (
        f"{ticker} 2025: the average-balance ROE ({on_average:.2%}) is now closer to "
        f"{_REFERENCE['platform']}'s {published:.2%} than the closing-balance one "
        f"({on_closing:.2%}) — ADR 0003's basis no longer matches the platforms"
    )


def test_the_not_compared_indicators_are_named_with_their_basis() -> None:
    # P/E, P/B and PSR are not measured against this platform, and the file has to say
    # why: it draws them on the dividend-adjusted price series, we do not (ADR 0018).
    not_compared = {k: v for k, v in _REFERENCE["not_compared"].items() if k != "_why"}
    assert not_compared, "the fixture must name what it declines to compare"
    for indicator, reason in not_compared.items():
        assert indicator not in _TOLERANCES
        assert reason.strip()
