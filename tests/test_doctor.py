"""Doctor use case: classify every persisted cell as value / named / unclassified."""

from datetime import UTC, date, datetime
from decimal import Decimal

from smaug.analysis.application.doctor import (
    CoverageScope,
    DoctorReport,
    DoctorUseCase,
    ExerciseCoverage,
    IndicatorCoverage,
    TickerCoverage,
)
from smaug.analysis.domain.entities import (
    VIEW_CLOSED_YEAR,
    VIEW_TTM,
    AnalysisView,
    TickerAnalysis,
)
from smaug.analysis.domain.indicators import (
    NULL_DISPOSITION_BY_REASON,
    Indicators,
    NullDisposition,
    NullReason,
    indicator_names,
    null_disposition,
)
from smaug.analysis.domain.ports import AnalysisStorageScope
from smaug.portfolio.domain.taxonomy import Classification
from tests.fakes import fake_sector_resolver

_DEFAULT_CLASSIFICATION = Classification("Commodities")


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


class ScopedFakeRepo(FakeRepo):
    """Adds the optional all-row scope read used by the SQL repository."""

    async def storage_scope(self, tickers: tuple[str, ...]) -> AnalysisStorageScope:
        return AnalysisStorageScope(persisted_rows=4, stale_rows=2, legacy_rows=1)


def _analysis(
    ticker: str,
    *,
    view: AnalysisView,
    reference_date: date,
    indicators: Indicators,
    classification: Classification = _DEFAULT_CLASSIFICATION,
) -> TickerAnalysis:
    return TickerAnalysis(
        ticker=ticker,
        classification=classification,
        reference_date=reference_date,
        computed_at=datetime(2026, 7, 10, tzinfo=UTC),
        indicators=indicators,
        view=view,
    )


def _cells(exercise) -> dict[str, str]:  # type: ignore[no-untyped-def]
    return {c.indicator: c.status for c in exercise.indicators}


def test_every_null_reason_has_one_stable_disposition() -> None:
    assert set(NULL_DISPOSITION_BY_REASON) == set(NullReason)
    assert all(
        null_disposition(reason) in set(NullDisposition) for reason in NullReason
    )


def test_doctor_totals_use_explicit_cell_and_null_denominators() -> None:
    report = DoctorReport(
        tickers=(
            TickerCoverage(
                ticker="PETR4",
                sector=fake_sector_resolver("PETR4"),
                exercises=(
                    ExerciseCoverage(
                        view=VIEW_TTM,
                        reference_date=date(2026, 6, 30),
                        indicators=(
                            IndicatorCoverage("value", True, None),
                            IndicatorCoverage(
                                "inapplicable", False, NullReason.INAPPLICABLE_REGIME
                            ),
                            IndicatorCoverage(
                                "undefined", False, NullReason.ZERO_DENOMINATOR
                            ),
                            IndicatorCoverage(
                                "source", False, NullReason.SOURCE_ACCOUNT_ABSENT
                            ),
                            IndicatorCoverage(
                                "recoverable", False, NullReason.MISSING_PRICE
                            ),
                        ),
                    ),
                ),
            ),
        ),
        scope=CoverageScope(1, 1, 0, 1),
    )

    totals = report.totals
    assert totals.total_cells == 5
    assert totals.values == 1
    assert totals.nulls == 4
    assert totals.inapplicable == 1
    assert totals.mathematically_undefined == 1
    assert totals.primary_source_unavailable == 1
    assert totals.recoverable_gap == 1
    assert totals.missing_or_recoverable == 2
    assert totals.missing_or_recoverable_pct_of_nulls == 50.0
    assert totals.missing_or_recoverable_pct_of_cells == 40.0
    assert totals.inapplicable_pct_of_nulls == 25.0
    assert totals.inapplicable_pct_of_cells == 20.0


async def test_doctor_preserves_requested_and_storage_scope_counts() -> None:
    report = await DoctorUseCase(
        ScopedFakeRepo(), sector_resolver=fake_sector_resolver
    ).execute(["PETR4", "TAEE11"])

    assert report.coverage_scope == CoverageScope(
        requested_tickers=2,
        persisted_tickers=0,
        no_analysis_tickers=2,
        persisted_exercises=0,
        stale_rows=2,
        legacy_rows=1,
    )


async def test_doctor_classifies_value_named_and_unclassified() -> None:
    indicators = Indicators(
        roe=Decimal("0.18"),  # a value
        net_margin=None,  # null, no reason → unclassified
        null_reasons={"pe_basic": NullReason.MISSING_PRICE},  # a named null
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

    report = await DoctorUseCase(repo, sector_resolver=fake_sector_resolver).execute(
        ["PETR4"]
    )

    (ticker_cov,) = report.tickers
    (exercise,) = ticker_cov.exercises
    # Every indicator is covered — no silent omission.
    assert len(exercise.indicators) == len(indicator_names())
    cells = _cells(exercise)
    assert cells["roe"] == "value"
    assert cells["pe_basic"] == "missing_price"
    assert cells["net_margin"] == "unclassified"
    assert exercise.values == 1
    assert exercise.named_nulls == 1
    assert exercise.unclassified == len(indicator_names()) - 2


async def test_doctor_names_missing_price_never_a_bare_null() -> None:
    """#42 in miniature: a closed year that lost its price reads as missing_price."""
    priced_out = dict.fromkeys(
        ("pe_basic", "pb", "company_pe", "psr", "dividend_yield", "ev_ebitda"),
        NullReason.MISSING_PRICE,
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
                    classification=Classification(
                        "Financeiro", "Intermediários Financeiros", "Bancos"
                    ),
                )
            ]
        }
    )

    report = await DoctorUseCase(repo, sector_resolver=fake_sector_resolver).execute(
        ["BBAS3"]
    )

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

    report = await DoctorUseCase(repo, sector_resolver=fake_sector_resolver).execute(
        ["WEGE3"]
    )

    views = [e.view for e in report.tickers[0].exercises]
    assert views == [VIEW_TTM, VIEW_CLOSED_YEAR, VIEW_CLOSED_YEAR]


async def test_doctor_reports_ticker_without_persisted_analysis() -> None:
    report = await DoctorUseCase(
        FakeRepo(), sector_resolver=fake_sector_resolver
    ).execute(["TAEE11"])

    (ticker_cov,) = report.tickers
    assert ticker_cov.ticker == "TAEE11"
    assert ticker_cov.exercises == ()


async def test_doctor_report_sums_unclassified_across_every_ticker() -> None:
    """#169: the exchange-scale coverage gate is this count reaching zero."""
    fully_named = Indicators(
        null_reasons=dict.fromkeys(indicator_names(), NullReason.MISSING_PRICE)
    )
    bare = Indicators()  # every field null, no reason → every cell unclassified
    repo = FakeRepo(
        latest={
            "PETR4": _analysis(
                "PETR4",
                view=VIEW_TTM,
                reference_date=date(2025, 9, 30),
                indicators=fully_named,
            )
        },
        history={
            "WEGE3": [
                _analysis(
                    "WEGE3",
                    view=VIEW_CLOSED_YEAR,
                    reference_date=date(2024, 12, 31),
                    indicators=bare,
                )
            ]
        },
    )

    report = await DoctorUseCase(repo, sector_resolver=fake_sector_resolver).execute(
        ["PETR4", "WEGE3"]
    )

    assert report.unclassified == len(indicator_names())
