"""Chart-of-accounts drift: an account that mapped in some years and not others."""

from dataclasses import replace
from datetime import date
from decimal import Decimal

from smaug.analysis.application.drift import AccountDriftUseCase
from smaug.analysis.domain.financials import StandardizedFinancials
from smaug.entrypoints.cli import format_drift
from smaug.portfolio.domain.sectors import Sector


class FakeReader:
    """The ``FundamentalsReader`` surface the drift report uses (annuals only)."""

    def __init__(self, annuals: dict[str, list[StandardizedFinancials]]) -> None:
        self._annuals = annuals

    async def history(self, ticker: str) -> list[StandardizedFinancials]:
        return []

    async def annuals(self, ticker: str) -> list[StandardizedFinancials]:
        return self._annuals.get(ticker, [])


def _year(year: int, **accounts: Decimal | None) -> StandardizedFinancials:
    return replace(
        StandardizedFinancials(
            reference_date=date(year, 12, 31),
            sector=Sector.BANK,
        ),
        **accounts,
    )


async def test_drift_flags_an_account_the_older_filings_do_not_map() -> None:
    # The #155 shape, which is what this report exists to have caught: net income
    # reads from 2020 on and not before, because the mapper's needle was written
    # against the chart the banks adopted that year.
    annuals = [
        _year(y, net_income=Decimal(100) if y >= 2020 else None, equity=Decimal(900))
        for y in range(2017, 2023)
    ]

    use_case = AccountDriftUseCase(FakeReader({"BBAS3": annuals}))
    report = await use_case.execute(["BBAS3"])

    (ticker,) = report.tickers
    (drift,) = ticker.accounts
    assert drift.account == "net_income"
    assert drift.missing == (2017, 2018, 2019)
    assert drift.read == (2020, 2021, 2022)
    assert drift.boundaries == 1
    assert drift.missing_side == "older"  # the mapper does not reach the old chart


async def test_drift_ignores_an_account_no_year_maps() -> None:
    # A bank files no borrowings line in any year, so ``total_debt`` is null in
    # all of them. That is the filer's schema, not a needle going stale — and
    # reporting it would bury the real signal under a page of steady-state nulls.
    annuals = [_year(y, equity=Decimal(900)) for y in range(2017, 2023)]

    use_case = AccountDriftUseCase(FakeReader({"BBAS3": annuals}))
    report = await use_case.execute(["BBAS3"])

    (ticker,) = report.tickers
    assert ticker.accounts == ()
    assert report.drifting == 0


async def test_drift_marks_an_account_the_recent_filings_stopped_mapping() -> None:
    # The urgent direction: the needle works on the old filings and not on what we
    # ingest today, so every new period will be wrong until someone looks.
    annuals = [
        _year(y, capex=Decimal(50) if y <= 2019 else None, equity=Decimal(900))
        for y in range(2017, 2023)
    ]

    use_case = AccountDriftUseCase(FakeReader({"BBSE3": annuals}))
    report = await use_case.execute(["BBSE3"])

    (drift,) = report.tickers[0].accounts
    assert drift.missing_side == "newer"
    assert drift.missing == (2020, 2021, 2022)


async def test_drift_counts_the_boundaries_of_an_intermittent_account() -> None:
    # More than one boundary is usually not a chart change but a line the filer
    # reports only in the years it has something to report — the count is what
    # lets a reader tell the two apart at a glance.
    present = {2017: True, 2018: False, 2019: True, 2020: False}
    annuals = [
        _year(y, capex=Decimal(50) if present[y] else None, equity=Decimal(900))
        for y in present
    ]

    use_case = AccountDriftUseCase(FakeReader({"WEGE3": annuals}))
    report = await use_case.execute(["WEGE3"])

    (drift,) = report.tickers[0].accounts
    assert drift.boundaries == 3
    assert drift.missing_side == "mixed"


async def test_drift_needs_two_years_to_compare() -> None:
    # One closed year has no transition to show; the report says nothing rather
    # than calling a single year's shape a change.
    report = await AccountDriftUseCase(
        FakeReader({"PETR4": [_year(2025, net_income=Decimal(100))]})
    ).execute(["PETR4"])

    assert report.tickers[0].accounts == ()


async def test_format_drift_compresses_years_and_leads_with_the_recent_break() -> None:
    # Ticker order follows the request, as the coverage section does — it is how a
    # reader finds one. Within a ticker the accounts are ranked by urgency.
    annuals = [
        _year(
            y,
            net_income=Decimal(100) if y >= 2020 else None,  # older side missing
            capex=Decimal(50) if y <= 2018 else None,  # newer side missing
        )
        for y in range(2017, 2023)
    ]

    text = format_drift(
        await AccountDriftUseCase(FakeReader({"BBAS3": annuals})).execute(["BBAS3"])
    )

    assert "missing 2017-2019" in text  # ranges, not six loose years
    assert "(older, 1 boundary)" in text
    assert "(newer, 1 boundary)" in text
    assert text.index("capex") < text.index("net_income")  # the newer break leads
