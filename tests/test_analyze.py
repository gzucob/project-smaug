"""Analysis use case: compute+save, skip on no data, degrade on price failure."""

from datetime import date
from decimal import Decimal

from smaug.analysis.application.analyze import AnalyzePortfolioUseCase
from smaug.analysis.domain.entities import TickerAnalysis
from smaug.analysis.domain.financials import (
    MarketData,
    StandardizedFinancials,
    YearPrices,
)
from smaug.portfolio.domain.sectors import Sector
from smaug.shared.errors import BrapiForbiddenError


class FakeReader:
    def __init__(self, history: dict[str, list[StandardizedFinancials]]) -> None:
        self._history = history

    async def history(self, ticker: str) -> list[StandardizedFinancials]:
        return self._history.get(ticker, [])


class FakePrice:
    def __init__(
        self,
        data: MarketData | None = None,
        *,
        year: YearPrices | None = None,
        error: Exception | None = None,
    ) -> None:
        self._data = data
        self._year = year
        self._error = error

    async def get(self, ticker: str) -> MarketData:
        if self._error is not None:
            raise self._error
        return self._data or MarketData()

    async def year_prices(self, ticker: str, year: int) -> YearPrices:
        if self._error is not None:
            raise self._error
        return self._year or YearPrices()


class FakeRepo:
    def __init__(self) -> None:
        self.saved: list[TickerAnalysis] = []

    async def save(self, analysis: TickerAnalysis) -> None:
        self.saved.append(analysis)

    async def latest(self, ticker: str) -> TickerAnalysis | None:
        return None

    async def all_latest(self) -> list[TickerAnalysis]:
        return list(self.saved)


def _petr4() -> StandardizedFinancials:
    return StandardizedFinancials(
        reference_date=date(2024, 9, 30),
        sector=Sector.COMMODITY,
        equity=Decimal(6000),
        net_income=Decimal(900),  # annualized -> 1200
    )


async def test_analyze_prices_multiples_on_year_adjusted_average() -> None:
    repo = FakeRepo()
    use_case = AnalyzePortfolioUseCase(
        FakeReader({"PETR4": [_petr4()]}),
        FakePrice(
            MarketData(price=Decimal(10), market_cap=Decimal(12000)),
            year=YearPrices(nominal_avg=Decimal(8), adjusted_avg=Decimal(6)),
        ),
        repo,
    )

    out = await use_case.execute(["PETR4"])

    assert len(out) == 1
    saved = repo.saved[0]
    assert saved.indicators.roe == Decimal("0.2")  # annualized 1200 / 6000
    # Multiples are repriced onto the year's adjusted average: the current cap
    # 12000 at price 10 becomes an effective cap of 12000 * 6 / 10 = 7200.
    assert saved.price == Decimal(6)  # adjusted-year average is the basis used
    assert saved.price_nominal == Decimal(8)
    assert saved.price_basis == "adjusted_year_avg"
    assert saved.indicators.pe == Decimal(6)  # 7200 / 1200
    assert saved.indicators.pb == Decimal("1.2")  # 7200 / 6000


async def test_analyze_nulls_multiples_when_year_price_missing() -> None:
    repo = FakeRepo()
    use_case = AnalyzePortfolioUseCase(
        FakeReader({"PETR4": [_petr4()]}),
        # Current quote is available, but the year has no adjusted average.
        FakePrice(MarketData(price=Decimal(10), market_cap=Decimal(12000))),
        repo,
    )

    await use_case.execute(["PETR4"])

    saved = repo.saved[0]
    assert saved.indicators.roe == Decimal("0.2")  # accounting indicator survives
    assert saved.indicators.pe is None  # no adjusted basis -> no market multiple
    assert saved.price is None
    assert saved.price_basis is None


async def test_analyze_skips_ticker_without_fundamentals() -> None:
    use_case = AnalyzePortfolioUseCase(FakeReader({}), FakePrice(), FakeRepo())
    assert await use_case.execute(["PETR4"]) == []


async def test_analyze_degrades_when_price_unavailable() -> None:
    bank = StandardizedFinancials(
        reference_date=date(2024, 9, 30),
        sector=Sector.BANK,
        equity=Decimal(8000),
        net_income=Decimal(600),  # annualized -> 800
    )
    use_case = AnalyzePortfolioUseCase(
        FakeReader({"BBAS3": [bank]}),
        FakePrice(error=BrapiForbiddenError("403")),
        FakeRepo(),
    )

    out = await use_case.execute(["BBAS3"])

    assert len(out) == 1
    assert out[0].indicators.roe == Decimal("0.1")  # fundamentals still computed
    assert out[0].indicators.pe is None  # no price -> no market multiple
    assert out[0].price is None
