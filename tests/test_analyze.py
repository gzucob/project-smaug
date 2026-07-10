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


class FakeShares:
    """CVM's filed share count, per fiscal year. Empty → the brapi fallback."""

    def __init__(self, by_year: dict[int, Decimal] | None = None) -> None:
        self._by_year = by_year or {}

    async def outstanding(self, ticker: str, year: int) -> Decimal | None:
        return self._by_year.get(year)


class FakeRepo:
    def __init__(self) -> None:
        self.saved: list[TickerAnalysis] = []

    async def save(self, analysis: TickerAnalysis) -> None:
        self.saved.append(analysis)

    async def latest(self, ticker: str) -> TickerAnalysis | None:
        return None

    async def all_latest(self) -> list[TickerAnalysis]:
        return [a for a in self.saved if a.view == "ttm_live"]

    async def history(self, ticker: str) -> list[TickerAnalysis]:
        return [a for a in self.saved if a.ticker == ticker and a.view == "closed_year"]


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
        FakeShares(),
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
        FakeShares(),
    )

    await use_case.execute(["PETR4"])

    ind = repo.saved[0].indicators
    assert ind.revenue_growth == Decimal("0.25")  # (4000 - 3200) / 3200
    assert ind.net_income_growth == Decimal("0.2")  # (1200 - 1000) / 1000


async def test_analyze_produces_ttm_and_closed_year_views() -> None:
    # A full TTM window plus two ingested DFPs (2024, 2025). The TTM is priced on
    # the current nominal quote; each closed year is priced on its dividend-
    # adjusted average, repricing the current market cap onto that basis.
    repo = FakeRepo()
    quarters = _quarters(
        Sector.COMMODITY, net_income=Decimal(300), equity=Decimal(6000)
    )
    annual_2024 = StandardizedFinancials(
        reference_date=date(2024, 12, 31),
        sector=Sector.COMMODITY,
        period_start=date(2024, 1, 1),
        net_income=Decimal(500),
        equity=Decimal(3000),
        revenue=Decimal(4000),
    )
    annual_2025 = StandardizedFinancials(
        reference_date=date(2025, 12, 31),
        sector=Sector.COMMODITY,
        period_start=date(2025, 1, 1),
        net_income=Decimal(600),
        equity=Decimal(3600),
        revenue=Decimal(5000),
    )
    use_case = AnalyzePortfolioUseCase(
        FakeReader({"PETR4": quarters}, annuals={"PETR4": [annual_2024, annual_2025]}),
        FakePrice(
            MarketData(price=Decimal(10), market_cap=Decimal(12000)),
            year=YearPrices(nominal_avg=Decimal(8), adjusted_avg=Decimal(6)),
        ),
        repo,
        FakeShares(),
    )

    out = await use_case.execute(["PETR4"])

    # TTM + two closed years, TTM saved first.
    assert len(out) == 3
    assert out[0].view == "ttm_live"
    views = {(a.view, a.reference_date): a for a in out}

    ttm = views[("ttm_live", date(2026, 3, 31))]
    assert ttm.price_basis == "ttm_current_nominal"
    assert ttm.price == Decimal(10)  # current nominal quote

    y2025 = views[("closed_year", date(2025, 12, 31))]
    assert y2025.price_basis == "adjusted_year_avg"
    assert y2025.price == Decimal(6)  # adjusted average
    assert y2025.price_nominal == Decimal(8)  # nominal average
    # effective cap = 12000 * 6 / 10 = 7200 → P/E = 7200/600 = 12, P/VP = 7200/3600 = 2
    assert y2025.indicators.pe == Decimal(12)
    assert y2025.indicators.pb == Decimal(2)
    # YoY vs the 2024 DFP: net income (600 - 500) / 500 = 0.2.
    assert y2025.indicators.net_income_growth == Decimal("0.2")

    # The oldest closed year has no prior DFP → growth degrades to null.
    y2024 = views[("closed_year", date(2024, 12, 31))]
    assert y2024.indicators.net_income_growth is None


async def test_analyze_skips_when_fewer_than_four_quarters() -> None:
    two = _quarters(Sector.COMMODITY, net_income=Decimal(300))[:2]
    use_case = AnalyzePortfolioUseCase(
        FakeReader({"PETR4": two}), FakePrice(), FakeRepo(), FakeShares()
    )
    assert await use_case.execute(["PETR4"]) == []


async def test_analyze_skips_ticker_without_fundamentals() -> None:
    use_case = AnalyzePortfolioUseCase(
        FakeReader({}), FakePrice(), FakeRepo(), FakeShares()
    )
    assert await use_case.execute(["PETR4"]) == []


async def test_analyze_divides_each_view_by_that_years_filed_shares() -> None:
    # CVM filed 600 shares for 2024 and 300 for the TTM year — the closed year
    # must not borrow the current count (that was the F8 approximation). The
    # annual is 2024, so it never doubles as the TTM's derived Q4.
    quarters = _quarters(
        Sector.COMMODITY, net_income=Decimal(300), equity=Decimal(6000)
    )
    annual_2024 = StandardizedFinancials(
        reference_date=date(2024, 12, 31),
        sector=Sector.COMMODITY,
        period_start=date(2024, 1, 1),
        net_income=Decimal(600),
        equity=Decimal(3600),
    )
    repo = FakeRepo()
    use_case = AnalyzePortfolioUseCase(
        FakeReader({"PETR4": quarters}, annuals={"PETR4": [annual_2024]}),
        FakePrice(
            MarketData(price=Decimal(10), market_cap=Decimal(12000)),
            year=YearPrices(nominal_avg=Decimal(8), adjusted_avg=Decimal(6)),
        ),
        repo,
        FakeShares({2024: Decimal(600), 2026: Decimal(300)}),
    )

    out = await use_case.execute(["PETR4"])
    views = {(a.view, a.reference_date): a for a in out}

    ttm = views[("ttm_live", date(2026, 3, 31))]
    assert ttm.indicators.eps == Decimal(4)  # TTM 1200 / 300 shares
    assert ttm.indicators.bvps == Decimal(20)  # 6000 / 300

    y2024 = views[("closed_year", date(2024, 12, 31))]
    assert y2024.indicators.eps == Decimal(1)  # 600 / 600 shares, not / 300
    assert y2024.indicators.bvps == Decimal(6)  # 3600 / 600


async def test_analyze_falls_back_to_the_quote_share_count() -> None:
    # No CVM capital ingested for this ticker: brapi's derived count carries it.
    repo = FakeRepo()
    use_case = AnalyzePortfolioUseCase(
        FakeReader(
            {
                "PETR4": _quarters(
                    Sector.COMMODITY, net_income=Decimal(300), equity=Decimal(6000)
                )
            }
        ),
        FakePrice(
            MarketData(
                price=Decimal(10), market_cap=Decimal(12000), shares=Decimal(1200)
            )
        ),
        repo,
        FakeShares(),
    )

    await use_case.execute(["PETR4"])

    assert repo.saved[0].indicators.eps == Decimal(1)  # 1200 / 1200
    assert repo.saved[0].indicators.bvps == Decimal(5)  # 6000 / 1200


async def test_analyze_keeps_per_share_indicators_when_price_is_missing() -> None:
    # eps/bvps need only the share count, so a price outage must not null them.
    repo = FakeRepo()
    use_case = AnalyzePortfolioUseCase(
        FakeReader(
            {
                "BBAS3": _quarters(
                    Sector.BANK, net_income=Decimal(200), equity=Decimal(8000)
                )
            }
        ),
        FakePrice(error=BrapiForbiddenError("403")),
        repo,
        FakeShares({2026: Decimal(400)}),
    )

    await use_case.execute(["BBAS3"])

    saved = repo.saved[0]
    assert saved.indicators.eps == Decimal(2)  # 800 / 400
    assert saved.indicators.bvps == Decimal(20)  # 8000 / 400
    assert saved.indicators.pe is None  # still no price


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
        FakeShares(),
    )

    out = await use_case.execute(["BBAS3"])

    assert len(out) == 1
    assert out[0].indicators.roe == Decimal("0.1")  # 800 / 8000, fundamentals survive
    assert out[0].indicators.pe is None  # no price -> no market multiple
    assert out[0].price is None
