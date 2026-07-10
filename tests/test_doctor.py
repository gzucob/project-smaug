"""Doctor use case: classify every persisted cell as value / named / unclassified."""

from datetime import UTC, date, datetime
from decimal import Decimal

from smaug.analysis.application.doctor import DoctorUseCase
from smaug.analysis.domain.entities import (
    VIEW_CLOSED_YEAR,
    VIEW_TTM,
    AnalysisView,
    TickerAnalysis,
)
from smaug.analysis.domain.indicators import (
    Indicators,
    NullReason,
    indicator_names,
)
from smaug.portfolio.domain.sectors import Sector


class FakeRepo:
    """Serves the persisted TTM and closed-year rows the use case reads back."""

    def __init__(
        self,
        latest: dict[str, TickerAnalysis] | None = None,
        history: dict[str, list[TickerAnalysis]] | None = None,
    ) -> None:
        self._latest = latest or {}
        self._history = history or {}

    async def save(self, analysis: TickerAnalysis) -> None: ...

    async def latest(self, ticker: str) -> TickerAnalysis | None:
        return self._latest.get(ticker)

    async def all_latest(self) -> list[TickerAnalysis]:
        return list(self._latest.values())

    async def history(self, ticker: str) -> list[TickerAnalysis]:
        return self._history.get(ticker, [])


def _analysis(
    ticker: str,
    *,
    view: AnalysisView,
    reference_date: date,
    indicators: Indicators,
    sector: Sector = Sector.COMMODITY,
) -> TickerAnalysis:
    return TickerAnalysis(
        ticker=ticker,
        sector=sector,
        reference_date=reference_date,
        computed_at=datetime(2026, 7, 10, tzinfo=UTC),
        indicators=indicators,
        view=view,
    )


def _cells(exercise) -> dict[str, str]:  # type: ignore[no-untyped-def]
    return {c.indicator: c.status for c in exercise.indicators}


async def test_doctor_classifies_value_named_and_unclassified() -> None:
    indicators = Indicators(
        roe=Decimal("0.18"),  # a value
        net_margin=None,  # null, no reason → unclassified
        null_reasons={"pe": NullReason.MISSING_PRICE},  # a named null
    )
    repo = FakeRepo(
        latest={
            "PETR4": _analysis(
                "PETR4",
                view=VIEW_TTM,
                reference_date=date(2025, 9, 30),
                indicators=indicators,
            )
        }
    )

    report = await DoctorUseCase(repo).execute(["PETR4"])

    (ticker_cov,) = report.tickers
    (exercise,) = ticker_cov.exercises
    # Every indicator is covered — no silent omission.
    assert len(exercise.indicators) == len(indicator_names())
    cells = _cells(exercise)
    assert cells["roe"] == "value"
    assert cells["pe"] == "missing_price"
    assert cells["net_margin"] == "unclassified"
    assert exercise.values == 1
    assert exercise.named_nulls == 1
    assert exercise.unclassified == len(indicator_names()) - 2


async def test_doctor_names_missing_price_never_a_bare_null() -> None:
    """#42 in miniature: a closed year that lost its price reads as missing_price."""
    priced_out = dict.fromkeys(
        ("pe", "pb", "psr", "dividend_yield", "ev_ebitda"), NullReason.MISSING_PRICE
    )
    indicators = Indicators(
        roe=Decimal("0.2"),
        revenue=Decimal("1000"),
        net_income=Decimal("200"),
        null_reasons=priced_out,
    )
    repo = FakeRepo(
        history={
            "BBAS3": [
                _analysis(
                    "BBAS3",
                    view=VIEW_CLOSED_YEAR,
                    reference_date=date(2024, 12, 31),
                    indicators=indicators,
                    sector=Sector.BANK,
                )
            ]
        }
    )

    report = await DoctorUseCase(repo).execute(["BBAS3"])

    (exercise,) = report.tickers[0].exercises
    cells = _cells(exercise)
    for name in priced_out:
        assert cells[name] == "missing_price"


async def test_doctor_lists_ttm_first_then_closed_years() -> None:
    repo = FakeRepo(
        latest={
            "WEGE3": _analysis(
                "WEGE3",
                view=VIEW_TTM,
                reference_date=date(2026, 3, 31),
                indicators=Indicators(),
            )
        },
        history={
            "WEGE3": [
                _analysis(
                    "WEGE3",
                    view=VIEW_CLOSED_YEAR,
                    reference_date=date(2023, 12, 31),
                    indicators=Indicators(),
                ),
                _analysis(
                    "WEGE3",
                    view=VIEW_CLOSED_YEAR,
                    reference_date=date(2024, 12, 31),
                    indicators=Indicators(),
                ),
            ]
        },
    )

    report = await DoctorUseCase(repo).execute(["WEGE3"])

    views = [e.view for e in report.tickers[0].exercises]
    assert views == [VIEW_TTM, VIEW_CLOSED_YEAR, VIEW_CLOSED_YEAR]


async def test_doctor_reports_ticker_without_persisted_analysis() -> None:
    report = await DoctorUseCase(FakeRepo()).execute(["TAEE11"])

    (ticker_cov,) = report.tickers
    assert ticker_cov.ticker == "TAEE11"
    assert ticker_cov.exercises == ()
