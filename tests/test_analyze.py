"""Analysis use case: build the TTM, price it nominally, skip/degrade gracefully."""

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

# Four consecutive quarter-ends: the TTM window Jul/2025–Mar/2026.
_QUARTER_ENDS = (
    date(2025, 6, 30),
    date(2025, 9, 30),
    date(2025, 12, 31),
    date(2026, 3, 31),
)


class FakeReader:
    def __init__(
        self,
        history: dict[str, list[StandardizedFinancials]],
        annuals: dict[str, list[StandardizedFinancials]] | None = None,
    ) -> None:
        self._history = history
        self._annuals = annuals or {}

    async def history(self, ticker: str) -> list[StandardizedFinancials]:
        return self._history.get(ticker, [])

    async def annuals(self, ticker: str) -> list[StandardizedFinancials]:
        return self._annuals.get(ticker, [])


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


def _quarters(
    sector: Sector,
    *,
    net_income: Decimal,
    equity: Decimal | None = None,
    ends: tuple[date, ...] = _QUARTER_ENDS,
) -> list[StandardizedFinancials]:
    """Isolated quarters (no ``period_start`` → taken as already isolated)."""
    return [
        StandardizedFinancials(
            reference_date=end,
            sector=sector,
            net_income=net_income,
            equity=equity,
        )
        for end in ends
    ]


async def test_analyze_builds_ttm_and_prices_on_current_nominal() -> None:
    repo = FakeRepo()
    use_case = AnalyzePortfolioUseCase(
        FakeReader(
            {
                "PETR4": _quarters(
                    Sector.COMMODITY, net_income=Decimal(300), equity=Decimal(6000)
                )
            }
        ),
        FakePrice(MarketData(price=Decimal(10), market_cap=Decimal(12000))),
        repo,
    )

    out = await use_case.execute(["PETR4"])

    assert len(out) == 1
    saved = repo.saved[0]
    # TTM net income = 4 * 300 = 1200 over 12 months → no annualization.
    assert saved.reference_date == date(2026, 3, 31)
    assert saved.indicators.roe == Decimal("0.2")  # 1200 / 6000
    assert saved.price == Decimal(10)  # current nominal quote
    assert saved.price_nominal == Decimal(10)
    assert saved.price_basis == "ttm_current_nominal"
    assert saved.indicators.pe == Decimal(10)  # 12000 / 1200
    assert saved.indicators.pb == Decimal(2)  # 12000 / 6000


async def test_analyze_computes_growth_against_prior_year_annual() -> None:
    # TTM window ends 2026-06-30 (year 2026); the prior closed year (2025 DFP) is
    # the year-over-year growth base. Only two quarters fall in 2025, so build_ttm
    # does not treat the annual as a Q4 source — it stays the growth comparator.
    ends = (
        date(2025, 9, 30),
        date(2025, 12, 31),
        date(2026, 3, 31),
        date(2026, 6, 30),
    )
    quarters = [
        StandardizedFinancials(
            reference_date=end,
            sector=Sector.COMMODITY,
            revenue=Decimal(1000),
            net_income=Decimal(300),
            equity=Decimal(6000),
        )
        for end in ends
    ]
    prior = StandardizedFinancials(
        reference_date=date(2025, 12, 31),
        sector=Sector.COMMODITY,
        revenue=Decimal(3200),
        net_income=Decimal(1000),
    )
    repo = FakeRepo()
    use_case = AnalyzePortfolioUseCase(
        FakeReader({"PETR4": quarters}, annuals={"PETR4": [prior]}),
        FakePrice(MarketData(price=Decimal(10), market_cap=Decimal(12000))),
        repo,
    )

    await use_case.execute(["PETR4"])

    ind = repo.saved[0].indicators
    assert ind.revenue_growth == Decimal("0.25")  # (4000 - 3200) / 3200
    assert ind.net_income_growth == Decimal("0.2")  # (1200 - 1000) / 1000


async def test_analyze_skips_when_fewer_than_four_quarters() -> None:
    two = _quarters(Sector.COMMODITY, net_income=Decimal(300))[:2]
    use_case = AnalyzePortfolioUseCase(
        FakeReader({"PETR4": two}), FakePrice(), FakeRepo()
    )
    assert await use_case.execute(["PETR4"]) == []


async def test_analyze_skips_ticker_without_fundamentals() -> None:
    use_case = AnalyzePortfolioUseCase(FakeReader({}), FakePrice(), FakeRepo())
    assert await use_case.execute(["PETR4"]) == []


async def test_analyze_degrades_when_price_unavailable() -> None:
    use_case = AnalyzePortfolioUseCase(
        FakeReader(
            {
                "BBAS3": _quarters(
                    Sector.BANK, net_income=Decimal(200), equity=Decimal(8000)
                )
            }
        ),
        FakePrice(error=BrapiForbiddenError("403")),
        FakeRepo(),
    )

    out = await use_case.execute(["BBAS3"])

    assert len(out) == 1
    assert out[0].indicators.roe == Decimal("0.1")  # 800 / 8000, fundamentals survive
    assert out[0].indicators.pe is None  # no price -> no market multiple
    assert out[0].price is None
