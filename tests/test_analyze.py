"""Analysis use case: build the TTM, price it nominally, skip/degrade gracefully."""

from datetime import date
from decimal import Decimal

from smaug.analysis.application.analyze import AnalyzePortfolioUseCase
from smaug.analysis.domain.entities import TickerAnalysis
from smaug.analysis.domain.financials import (
    MarketData,
    ShareCounts,
    StandardizedFinancials,
    YearPrices,
)
from smaug.analysis.domain.indicators import NullReason
from smaug.portfolio.domain.sectors import Sector
from smaug.portfolio.domain.share_classes import is_unit
from smaug.shared.errors import BrapiForbiddenError, BrapiTimeoutError

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
        by_symbol: dict[str, MarketData] | None = None,
        year_by_symbol: dict[str, YearPrices] | None = None,
        error: Exception | None = None,
        get_error: Exception | None = None,
        year_error: Exception | None = None,
    ) -> None:
        self._data = data
        self._year = year
        # ``by_symbol``/``year_by_symbol`` price each share class differently — the
        # multi-class cap sums PETR3 and PETR4 at their own quotes. Without them
        # every symbol gets the same price, which is enough for most tests.
        self._by_symbol = by_symbol
        self._year_by_symbol = year_by_symbol
        # ``error`` fails both sides; ``get_error``/``year_error`` fail one only,
        # so a test can knock out the live quote while the year history survives.
        self._get_error = get_error if get_error is not None else error
        self._year_error = year_error if year_error is not None else error

    async def get(self, ticker: str) -> MarketData:
        if self._get_error is not None:
            raise self._get_error
        if self._by_symbol is not None:
            return self._by_symbol.get(ticker, MarketData())
        return self._data or MarketData()

    async def year_prices(self, ticker: str, year: int) -> YearPrices:
        if self._year_error is not None:
            raise self._year_error
        if self._year_by_symbol is not None:
            return self._year_by_symbol.get(ticker, YearPrices())
        return self._year or YearPrices()


class FakeShares:
    """CVM's filed capital composition, per fiscal year (ON/PN + the filer's total)."""

    def __init__(self, by_year: dict[int, ShareCounts] | None = None) -> None:
        self._by_year = by_year or {}

    async def outstanding(self, ticker: str, year: int) -> Decimal | None:
        if is_unit(ticker):
            return None
        filed = self._by_year.get(year)
        return filed.total if filed is not None else None

    async def counts(self, ticker: str, year: int) -> ShareCounts | None:
        return self._by_year.get(year)


def _counts(*, common: int, preferred: int = 0) -> ShareCounts:
    """A filed capital composition. A class with no shares is absent, not zero."""
    return ShareCounts(
        common=Decimal(common),
        preferred=Decimal(preferred) if preferred else None,
        total=Decimal(common + preferred),
    )


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
        FakePrice(MarketData(price=Decimal(10))),
        repo,
        FakeShares({2026: _counts(common=800, preferred=400)}),
    )

    out = await use_case.execute(["PETR4"])

    assert len(out) == 1
    saved = repo.saved[0]
    # TTM net income = 4 * 300 = 1200 over 12 months → no annualization.
    assert saved.reference_date == date(2026, 3, 31)
    assert saved.indicators.roe == Decimal("0.2")  # 1200 / 6000
    assert saved.price == Decimal(10)  # current nominal quote
    assert saved.price_adjusted is None  # nothing paid out since a live quote
    assert saved.price_basis == "ttm_current_nominal"
    # Both classes quote at 10 here → cap = 10 × (800 + 400) = 12000.
    assert saved.indicators.pe == Decimal(10)  # 12000 / 1200
    assert saved.indicators.pb == Decimal(2)  # 12000 / 6000


async def test_analyze_sums_the_ttm_cap_over_the_listed_share_classes() -> None:
    # PETR3 (ON) and PETR4 (PN) each trade at their own price, so Petrobras is
    # worth 12 × 800 + 10 × 400 = 13600 — not the analyzed ticker's quote times
    # every share the company filed (10 × 1200 = 12000), which is what the old
    # single-quote cap paid and what made PETR4 land ~7% off (ADR 0014, #39).
    repo = FakeRepo()
    use_case = AnalyzePortfolioUseCase(
        FakeReader(
            {
                "PETR4": _quarters(
                    Sector.COMMODITY, net_income=Decimal(300), equity=Decimal(6800)
                )
            }
        ),
        FakePrice(
            by_symbol={
                "PETR3": MarketData(price=Decimal(12)),
                "PETR4": MarketData(price=Decimal(10)),
            }
        ),
        repo,
        FakeShares({2026: _counts(common=800, preferred=400)}),
    )

    await use_case.execute(["PETR4"])

    saved = repo.saved[0]
    assert saved.price == Decimal(10)  # the analyzed ticker's own quote, unchanged
    assert saved.indicators.pb == Decimal(2)  # cap 13600 / 6800, not 12000 / 6800
    assert saved.indicators.eps == Decimal(1)  # 1200 / 1200 — the filed total


async def test_analyze_capitalizes_a_unit_from_its_underlying_classes() -> None:
    # A unit's quote prices a bundle, so there is no share count to multiply it by
    # and the single-quote cap left SAPR11 with every multiple null. Summing the
    # underlying classes (SAPR3 ON + SAPR4 PN) capitalizes the company without
    # modelling the bundle at all (ADR 0014). The per-share indicators still need
    # that composition, so they stay null — with a named cause (#38).
    repo = FakeRepo()
    use_case = AnalyzePortfolioUseCase(
        FakeReader(
            {
                "SAPR11": _quarters(
                    Sector.UTILITY, net_income=Decimal(250), equity=Decimal(5500)
                )
            }
        ),
        FakePrice(
            by_symbol={
                "SAPR3": MarketData(price=Decimal(8)),
                "SAPR4": MarketData(price=Decimal(7)),
                "SAPR11": MarketData(price=Decimal(22)),  # the bundle's own price
            }
        ),
        repo,
        FakeShares({2026: _counts(common=500, preferred=1000)}),
    )

    await use_case.execute(["SAPR11"])

    saved = repo.saved[0]
    assert saved.price == Decimal(22)  # the unit quote is what the holder sees
    # cap = 8 × 500 + 7 × 1000 = 11000; TTM net income = 4 × 250 = 1000.
    assert saved.indicators.pe == Decimal(11)
    assert saved.indicators.pb == Decimal(2)  # 11000 / 5500
    assert saved.indicators.eps is None
    assert saved.indicators.null_reasons["eps"] is NullReason.MISSING_SHARE_COUNT


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
        FakePrice(MarketData(price=Decimal(10))),
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
    # adjusted average, with the cap built from that year's price and filed shares.
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
            MarketData(price=Decimal(10)),
            year=YearPrices(nominal_avg=Decimal(8), adjusted_avg=Decimal(6)),
        ),
        repo,
        FakeShares(
            {
                2024: _counts(common=800, preferred=400),
                2025: _counts(common=800, preferred=400),
            }
        ),
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
    assert y2025.price_basis == "nominal_year_avg"
    assert y2025.price == Decimal(8)  # what the shares traded at that year
    assert y2025.price_adjusted == Decimal(6)  # the total-return ruler, kept aside
    # cap = nominal_avg × shares(2025) = 8 × 1200 = 9600 (ADR 0018)
    #   → P/E = 9600/600 = 16, P/VP = 9600/3600
    assert y2025.indicators.pe == Decimal(16)
    assert y2025.indicators.pb == Decimal(9600) / Decimal(3600)
    # YoY vs the 2024 DFP: net income (600 - 500) / 500 = 0.2.
    assert y2025.indicators.net_income_growth == Decimal("0.2")

    # The oldest closed year has no prior DFP → growth degrades to null.
    y2024 = views[("closed_year", date(2024, 12, 31))]
    assert y2024.indicators.net_income_growth is None


async def test_a_closed_years_multiples_divide_by_what_the_shares_traded_at() -> None:
    # ADR 0018. The dividend-adjusted series discounts every past price by the payouts
    # made since, so for a heavy payer it collapses: PETR4's 2022 average reads R$13.15
    # adjusted against R$30.67 nominal, which turned its dividend yield into 106% — a
    # number that cannot describe how the market valued the company that year. The
    # valuation multiples divide by the nominal average; the adjusted one is kept
    # beside them, for return comparisons, and never reaches the cap.
    quarters = _quarters(Sector.COMMODITY, net_income=Decimal(300), equity=Decimal(600))
    annual = StandardizedFinancials(
        reference_date=date(2024, 12, 31),
        sector=Sector.COMMODITY,
        period_start=date(2024, 1, 1),
        net_income=Decimal(100),
        equity=Decimal(600),
        dividends_paid=Decimal(400),
    )
    repo = FakeRepo()
    use_case = AnalyzePortfolioUseCase(
        FakeReader({"PETR4": quarters}, annuals={"PETR4": [annual]}),
        FakePrice(
            MarketData(price=Decimal(10)),
            # The adjusted average is a third of the nominal one — a PETR4-shaped gap.
            year=YearPrices(nominal_avg=Decimal(30), adjusted_avg=Decimal(10)),
        ),
        repo,
        FakeShares({2024: _counts(common=60, preferred=40)}),  # 100 shares in all
    )

    await use_case.execute(["PETR4"])
    year = next(a for a in repo.saved if a.view == "closed_year")

    # cap = 30 × 100 = 3000 → P/E 30, DY 13.3%. On the adjusted basis the same year
    # would read cap 1000, P/E 10 and a 40% yield.
    assert year.price == Decimal(30)
    assert year.price_adjusted == Decimal(10)
    assert year.indicators.pe == Decimal(30)
    assert year.indicators.dividend_yield == Decimal(400) / Decimal(3000)


async def test_analyze_prices_closed_year_without_the_live_quote() -> None:
    # brapi (the live quote) is down, but Yahoo has the year's price and CVM has
    # the filed share count — the closed-year multiples must still compute, while
    # the live TTM view degrades independently (ADR 0012 / #66).
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
            get_error=BrapiTimeoutError("quote down"),
            year=YearPrices(nominal_avg=Decimal(8), adjusted_avg=Decimal(6)),
        ),
        repo,
        FakeShares({2024: _counts(common=800, preferred=400)}),
    )

    await use_case.execute(["PETR4"])
    views = {(a.view, a.reference_date): a for a in repo.saved}

    y2024 = views[("closed_year", date(2024, 12, 31))]
    assert y2024.price == Decimal(8)  # Yahoo nominal average, no brapi quote
    assert y2024.indicators.pe == Decimal(16)  # cap 8 × 1200 = 9600 / 600
    assert y2024.indicators.pb == Decimal(9600) / Decimal(3600)

    # The live view still degrades: it legitimately needs the current quote.
    ttm = views[("ttm_live", date(2026, 3, 31))]
    assert ttm.price is None
    assert ttm.indicators.pe is None


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
            MarketData(price=Decimal(10)),
            year=YearPrices(nominal_avg=Decimal(8), adjusted_avg=Decimal(6)),
        ),
        repo,
        FakeShares(
            {
                2024: _counts(common=400, preferred=200),
                2026: _counts(common=200, preferred=100),
            }
        ),
    )

    out = await use_case.execute(["PETR4"])
    views = {(a.view, a.reference_date): a for a in out}

    ttm = views[("ttm_live", date(2026, 3, 31))]
    assert ttm.indicators.eps == Decimal(4)  # TTM 1200 / 300 shares
    assert ttm.indicators.bvps == Decimal(20)  # 6000 / 300

    y2024 = views[("closed_year", date(2024, 12, 31))]
    assert y2024.indicators.eps == Decimal(1)  # 600 / 600 shares, not / 300
    assert y2024.indicators.bvps == Decimal(6)  # 3600 / 600


async def test_analyze_refuses_the_quotes_own_cap_and_share_count() -> None:
    # brapi's quote carries a company-wide market cap and a share count derived
    # from it, so for a multi-class ticker the identity cap ≡ price × shares does
    # not hold: PETR4 landed +6.7% off the filed count. With no CVM filing there
    # is no honest count, so both the cap and the per-share indicators go null
    # with a named cause rather than take the vendor's biased pair (#39).
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
        FakeShares(),  # CVM filed nothing for this ticker
    )

    await use_case.execute(["PETR4"])

    ind = repo.saved[0].indicators
    assert ind.eps is None
    assert ind.pe is None  # brapi's 12000 is not borrowed
    assert ind.null_reasons["eps"] is NullReason.MISSING_SHARE_COUNT
    assert ind.null_reasons["pe"] is NullReason.MISSING_SHARE_COUNT


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
        FakeShares({2026: _counts(common=400)}),  # BBAS3 lists ON only
    )

    await use_case.execute(["BBAS3"])

    saved = repo.saved[0]
    assert saved.indicators.eps == Decimal(2)  # 800 / 400
    assert saved.indicators.bvps == Decimal(20)  # 8000 / 400
    assert saved.indicators.pe is None  # still no price
    assert saved.indicators.null_reasons["pe"] is NullReason.MISSING_PRICE


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


async def test_analyze_degrades_when_price_times_out() -> None:
    # A transport timeout is a BrapiError, so it degrades like a plan-gate 403:
    # market multiples go null, accounting indicators survive.
    use_case = AnalyzePortfolioUseCase(
        FakeReader(
            {
                "BBAS3": _quarters(
                    Sector.BANK, net_income=Decimal(200), equity=Decimal(8000)
                )
            }
        ),
        FakePrice(error=BrapiTimeoutError("read timed out")),
        FakeRepo(),
        FakeShares(),
    )

    out = await use_case.execute(["BBAS3"])

    assert len(out) == 1
    assert out[0].indicators.roe == Decimal("0.1")  # fundamentals survive
    assert out[0].indicators.pe is None  # timeout -> no market multiple
    assert out[0].price is None
