"""Analysis use case: build the TTM, price it nominally, skip/degrade gracefully."""

from datetime import UTC, date, datetime
from decimal import Decimal

from smaug.analysis.application.analyze import (
    AnalysisStatus,
    AnalyzePortfolioUseCase,
    NoAnalysisReason,
)
from smaug.analysis.domain.capital import RestatementStep
from smaug.analysis.domain.dividends import CashEvent
from smaug.analysis.domain.entities import TickerAnalysis
from smaug.analysis.domain.financials import (
    MarketData,
    SessionClose,
    ShareCountProvenance,
    ShareCounts,
    StandardizedFinancials,
    YearPrices,
)
from smaug.analysis.domain.indicators import NullReason
from smaug.analysis.domain.outcomes import AnalysisOutcome
from smaug.portfolio.domain.sectors import Sector
from smaug.portfolio.domain.share_classes import (
    PerShareClass,
    ShareClass,
    ShareKind,
    UnitComponent,
)
from smaug.shared.errors import (
    SourceForbiddenError,
    SourceMalformedError,
    SourceTimeoutError,
)
from tests.fakes import fake_classes_resolver

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
        year_by_symbol_and_year: dict[tuple[str, int], YearPrices] | None = None,
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
        # ``year_by_symbol_and_year`` additionally varies by year — needed for a
        # class whose price appears only from some year on (TAEE4 first trades in
        # 2017, #164), which ``year_by_symbol`` cannot express since it answers the
        # same way for every year asked.
        self._year_by_symbol_and_year = year_by_symbol_and_year
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
        if self._year_by_symbol_and_year is not None:
            return self._year_by_symbol_and_year.get((ticker, year), YearPrices())
        if self._year_by_symbol is not None:
            return self._year_by_symbol.get(ticker, YearPrices())
        return self._year or YearPrices()


class FakeShares:
    """CVM's filed capital composition, per fiscal year (ON/PN + the filer's total)."""

    def __init__(self, by_year: dict[int, ShareCounts] | None = None) -> None:
        self._by_year = by_year or {}

    async def outstanding(self, ticker: str, year: int) -> Decimal | None:
        if ticker in {"SAPR11", "TAEE11"}:
            return None
        filed = self._by_year.get(year)
        return filed.total if filed is not None else None

    def outstanding_null_reason(self, ticker: str, year: int) -> NullReason | None:
        return None

    async def counts(self, ticker: str, year: int) -> ShareCounts | None:
        return self._by_year.get(year)

    async def restatement_timeline(self, ticker: str) -> tuple[RestatementStep, ...]:
        # These counts are already on one base — the use case never restates.
        return ()


class FallbackSharesWithStrictSurface(FakeShares):
    """Expose the ADR 0017 fallback beside an opt-in strict surface."""

    async def strict_counts(self, ticker: str, year: int) -> ShareCounts | None:
        return None

    async def strict_outstanding(self, ticker: str, year: int) -> Decimal | None:
        return None

    async def capital_provenance(self, ticker: str, year: int) -> ShareCountProvenance:
        return ShareCountProvenance(
            requested_year=year,
            filed_year=year,
            status="missing_treasury_composition",
            issued=self._by_year.get(year),
        )


class FakeCashEvents:
    def __init__(self, by_class: dict[PerShareClass, tuple[CashEvent, ...]]) -> None:
        self._by_class = by_class

    async def cash_events(
        self, ticker: str, *, per_share_class: PerShareClass | None = None
    ) -> tuple[CashEvent, ...]:
        if per_share_class is None:
            return ()
        return self._by_class.get(per_share_class, ())


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


class FakeOutcomeRepo(FakeRepo):
    """Analysis repository with the optional durable outcome surface."""

    def __init__(self) -> None:
        super().__init__()
        self.outcomes: list[AnalysisOutcome] = []

    async def save_outcome(self, outcome: AnalysisOutcome) -> None:
        self.outcomes.append(outcome)


async def test_analyze_persists_stable_outcomes_for_analyzed_and_skipped_tickers() -> (
    None
):
    repo = FakeOutcomeRepo()
    recorded_at = datetime(2026, 8, 28, 12, 0, tzinfo=UTC)
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
        clock=lambda: recorded_at,
        id_factory=lambda: "analysis-run-1",
        classes_resolver=fake_classes_resolver,
    )

    run = await use_case.execute(["PETR4", "EMPTY3", "PETR4", "EMPTY3"])

    assert run.run_id == "analysis-run-1"
    assert run.recorded_at == recorded_at
    assert len(repo.outcomes) == 2
    assert [outcome.ticker for outcome in run.outcomes] == ["PETR4", "EMPTY3"]
    assert [analysis.ticker for analysis in repo.saved] == ["PETR4"]
    by_ticker = {outcome.ticker: outcome for outcome in repo.outcomes}
    assert by_ticker["PETR4"].status is AnalysisStatus.ANALYZED
    assert by_ticker["PETR4"].no_analysis_reason is None
    assert by_ticker["EMPTY3"].status is AnalysisStatus.SKIPPED
    assert (
        by_ticker["EMPTY3"].no_analysis_reason
        is NoAnalysisReason.NO_MIRRORED_FUNDAMENTALS
    )
    assert by_ticker["EMPTY3"].detail == "no CVM fundamentals are mirrored"
    assert {outcome.run_id for outcome in repo.outcomes} == {run.run_id}
    assert {outcome.recorded_at for outcome in repo.outcomes} == {recorded_at}
    assert all(outcome.run_id == run.run_id for outcome in run.outcomes)
    assert all(outcome.recorded_at == recorded_at for outcome in run.outcomes)


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
        classes_resolver=fake_classes_resolver,
    )

    out = (await use_case.execute(["PETR4"])).analyses

    assert len(out) == 1
    saved = repo.saved[0]
    # TTM net income = 4 * 300 = 1200 over 12 months → no annualization.
    assert saved.reference_date == date(2026, 3, 31)
    assert saved.indicators.roe == Decimal("0.2")  # 1200 / 6000
    assert saved.price == Decimal(10)  # current nominal quote
    assert saved.price_adjusted is None  # nothing paid out since a live quote
    assert saved.price_basis == "b3_latest_close"
    assert saved.share_count_basis == "cvm_latest_filed_outstanding_current_base"
    assert saved.liquidity_basis == "cpc03_cash_and_cash_equivalents"
    assert saved.debt_basis == "cvm_bpp_explicit_interest_bearing"
    assert saved.roic_tax_basis == "br_statutory_34pct"
    # Both classes quote at 10 here → cap = 10 × (800 + 400) = 12000.
    assert saved.indicators.company_pe == Decimal(10)  # 12000 / 1200
    assert saved.indicators.company_pb == Decimal(2)  # 12000 / 6000


async def test_analyze_uses_issued_fallback_when_treasury_is_unreconciled() -> None:
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
        FallbackSharesWithStrictSurface({2026: _counts(common=800, preferred=400)}),
        classes_resolver=fake_classes_resolver,
    )

    await use_case.execute(["PETR4"])

    saved = repo.saved[0]
    assert saved.capital_provenance is not None
    assert saved.capital_provenance.status == "missing_treasury_composition"
    assert saved.indicators.shares == Decimal(1200)
    assert saved.indicators.bvps == Decimal(5)
    assert saved.indicators.eps_basic_market == Decimal(1)
    assert saved.indicators.market_cap == Decimal(12000)
    assert saved.indicators.company_pe == Decimal(10)
    assert "company_pe" not in saved.indicators.null_reasons


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
        classes_resolver=fake_classes_resolver,
    )

    await use_case.execute(["PETR4"])

    saved = repo.saved[0]
    assert saved.price == Decimal(10)  # the analyzed ticker's own quote, unchanged
    assert saved.indicators.company_pb == Decimal(2)
    assert [(value.symbol, value.value) for value in saved.class_market_values] == [
        ("PETR3", Decimal("9600")),
        ("PETR4", Decimal("4000")),
    ]
    assert saved.indicators.eps is None
    assert saved.indicators.null_reasons["eps"] is (NullReason.MISSING_CPC41_DISCLOSURE)


async def test_analyze_capitalizes_a_unit_from_its_underlying_classes() -> None:
    # A unit's quote prices a bundle, so there is no share count to multiply it by
    # and the single-quote cap left SAPR11 with every multiple null. Summing the
    # underlying classes (SAPR3 ON + SAPR4 PN) capitalizes the company without
    # modelling the bundle at all (ADR 0014). This TTM still lacks a reconciled
    # weighted denominator, so CPC 41 stays null with that specific cause.
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
        classes_resolver=fake_classes_resolver,
    )

    await use_case.execute(["SAPR11"])

    saved = repo.saved[0]
    assert saved.price == Decimal(22)  # the unit quote is what the holder sees
    # cap = 8 × 500 + 7 × 1000 = 11000; TTM net income = 4 × 250 = 1000.
    assert saved.indicators.company_pe == Decimal(11)
    assert saved.indicators.company_pb == Decimal(2)  # 11000 / 5500
    assert saved.indicators.eps is None
    assert saved.indicators.null_reasons["eps"] is (NullReason.MISSING_CPC41_DISCLOSURE)


async def test_sibling_classes_keep_company_scope_but_get_own_multiples() -> None:
    on = StandardizedFinancials(
        reference_date=date(2024, 12, 31),
        period_start=date(2024, 1, 1),
        sector=Sector.COMMODITY,
        net_income=Decimal(1200),
        eps_basic=Decimal("1.50"),
        eps_diluted=Decimal("1.45"),
        equity=Decimal(6800),
    )
    pn = StandardizedFinancials(
        reference_date=date(2024, 12, 31),
        period_start=date(2024, 1, 1),
        sector=Sector.COMMODITY,
        net_income=Decimal(1200),
        eps_basic=Decimal("1.40"),
        eps_diluted=Decimal("1.35"),
        equity=Decimal(6800),
    )
    events = FakeCashEvents(
        {
            PerShareClass.ORDINARY: (
                CashEvent(effective=date(2024, 5, 2), amount_per_share=Decimal("0.50")),
            ),
            PerShareClass.PREFERRED: (
                CashEvent(effective=date(2024, 5, 2), amount_per_share=Decimal("0.80")),
            ),
        }
    )
    classes = (
        ShareClass("PETR3", ShareKind.COMMON),
        ShareClass("PETR4", ShareKind.PREFERRED),
    )
    repo = FakeRepo()
    use_case = AnalyzePortfolioUseCase(
        FakeReader({}, annuals={"PETR3": [on], "PETR4": [pn]}),
        FakePrice(
            year_by_symbol={
                "PETR3": YearPrices(nominal_avg=Decimal(12), closing=Decimal(12)),
                "PETR4": YearPrices(nominal_avg=Decimal(10), closing=Decimal(10)),
            }
        ),
        repo,
        FakeShares({2024: _counts(common=800, preferred=400)}),
        classes_resolver=lambda ticker: classes,
        cash_event_reader=events,
        per_share_resolver=lambda ticker: (
            UnitComponent(
                1,
                PerShareClass.ORDINARY
                if ticker == "PETR3"
                else PerShareClass.PREFERRED,
                ticker,
            ),
        ),
    )

    await use_case.execute(["PETR3", "PETR4"])
    by_ticker = {analysis.ticker: analysis for analysis in repo.saved}

    petr3 = by_ticker["PETR3"].indicators
    petr4 = by_ticker["PETR4"].indicators
    assert petr3.pe_basic == Decimal(8)
    assert petr4.pe_basic == Decimal(10) / Decimal("1.40")
    assert petr3.pb == Decimal(12) / (Decimal(6800) / Decimal(1200))
    assert petr4.pb == Decimal(10) / (Decimal(6800) / Decimal(1200))
    assert petr3.dividend_yield == Decimal("0.50") / Decimal(12)
    assert petr4.dividend_yield == Decimal("0.80") / Decimal(10)
    assert petr3.company_pe == petr4.company_pe
    assert petr3.company_pb == petr4.company_pb


async def test_unit_dividend_yield_composes_each_fca_component() -> None:
    annual = StandardizedFinancials(
        reference_date=date(2024, 12, 31),
        period_start=date(2024, 1, 1),
        sector=Sector.UTILITY,
        net_income=Decimal(500),
        equity=Decimal(5500),
    )
    events = FakeCashEvents(
        {
            PerShareClass.ORDINARY: (
                CashEvent(effective=date(2024, 3, 1), amount_per_share=Decimal("0.10")),
            ),
            PerShareClass.PREFERRED: (
                CashEvent(effective=date(2024, 3, 1), amount_per_share=Decimal("0.20")),
            ),
        }
    )
    repo = FakeRepo()
    use_case = AnalyzePortfolioUseCase(
        FakeReader({}, annuals={"SAPR11": [annual]}),
        FakePrice(
            year_by_symbol={
                "SAPR3": YearPrices(nominal_avg=Decimal(8), closing=Decimal(8)),
                "SAPR4": YearPrices(nominal_avg=Decimal(7), closing=Decimal(7)),
                "SAPR11": YearPrices(nominal_avg=Decimal(22), closing=Decimal(22)),
            }
        ),
        repo,
        FakeShares({2024: _counts(common=500, preferred=1000)}),
        classes_resolver=fake_classes_resolver,
        cash_event_reader=events,
        per_share_resolver=lambda ticker: (
            UnitComponent(1, PerShareClass.ORDINARY, "SAPR3"),
            UnitComponent(4, PerShareClass.PREFERRED, "SAPR4"),
        ),
    )

    await use_case.execute(["SAPR11"])

    indicators = repo.saved[0].indicators
    assert indicators.distributions_per_security == Decimal("0.90")
    assert indicators.dividend_yield == Decimal("0.90") / Decimal(22)


async def test_analyze_compares_ttm_growth_with_the_prior_comparable_ttm() -> None:
    # Current TTM through Q2/2026 is Q3/Q4 2025 + Q1/Q2 2026 = 1200.
    # Its prior comparable TTM is Q3/Q4 2024 + Q1/Q2 2025 = 600. Q4 in each
    # window is reconstructed from the respective DFP, as it is in CVM filings.
    quarters = [
        StandardizedFinancials(
            reference_date=date(year, month, day),
            sector=Sector.COMMODITY,
            revenue=Decimal(value),
            net_income=Decimal(value) / Decimal(10),
            equity=Decimal(6000),
        )
        for year, month, day, value in (
            (2024, 3, 31, 100),
            (2024, 6, 30, 100),
            (2024, 9, 30, 100),
            (2025, 3, 31, 200),
            (2025, 6, 30, 200),
            (2025, 9, 30, 200),
            (2026, 3, 31, 400),
            (2026, 6, 30, 400),
        )
    ]
    annuals = [
        StandardizedFinancials(
            reference_date=date(year, 12, 31),
            sector=Sector.COMMODITY,
            revenue=Decimal(revenue),
            net_income=Decimal(revenue) / Decimal(10),
        )
        for year, revenue in ((2024, 400), (2025, 800))
    ]
    repo = FakeRepo()
    use_case = AnalyzePortfolioUseCase(
        FakeReader({"PETR4": quarters}, annuals={"PETR4": annuals}),
        FakePrice(MarketData(price=Decimal(10))),
        repo,
        FakeShares(),
        classes_resolver=fake_classes_resolver,
    )

    await use_case.execute(["PETR4"])

    ind = repo.saved[0].indicators
    assert ind.revenue_growth == Decimal(1)  # (1200 - 600) / 600
    assert ind.net_income_growth == Decimal(1)


async def test_analyze_does_not_fall_back_to_an_annual_for_ttm_growth() -> None:
    quarters = [
        StandardizedFinancials(
            reference_date=end,
            sector=Sector.COMMODITY,
            revenue=Decimal(1000),
            net_income=Decimal(300),
        )
        for end in (
            date(2025, 9, 30),
            date(2025, 12, 31),
            date(2026, 3, 31),
            date(2026, 6, 30),
        )
    ]
    prior_annual = StandardizedFinancials(
        reference_date=date(2025, 12, 31),
        sector=Sector.COMMODITY,
        revenue=Decimal(3200),
        net_income=Decimal(1000),
    )
    repo = FakeRepo()
    use_case = AnalyzePortfolioUseCase(
        FakeReader({"PETR4": quarters}, annuals={"PETR4": [prior_annual]}),
        FakePrice(MarketData(price=Decimal(10))),
        repo,
        FakeShares(),
        classes_resolver=fake_classes_resolver,
    )

    await use_case.execute(["PETR4"])

    ind = repo.saved[0].indicators
    assert ind.revenue_growth is None
    assert ind.net_income_growth is None
    assert ind.null_reasons["revenue_growth"] is NullReason.MISSING_PRIOR_PERIOD
    assert ind.null_reasons["net_income_growth"] is NullReason.MISSING_PRIOR_PERIOD


async def test_december_ttm_growth_matches_closed_year_growth() -> None:
    quarters = [
        StandardizedFinancials(
            reference_date=date(year, month, day),
            sector=Sector.COMMODITY,
            revenue=Decimal(revenue),
            net_income=Decimal(revenue) / Decimal(10),
        )
        for year, revenue in ((2024, 100), (2025, 200))
        for month, day in ((3, 31), (6, 30), (9, 30))
    ]
    annuals = [
        StandardizedFinancials(
            reference_date=date(year, 12, 31),
            sector=Sector.COMMODITY,
            revenue=Decimal(revenue),
            net_income=Decimal(revenue) / Decimal(10),
        )
        for year, revenue in ((2024, 400), (2025, 800))
    ]
    repo = FakeRepo()
    use_case = AnalyzePortfolioUseCase(
        FakeReader({"PETR4": quarters}, annuals={"PETR4": annuals}),
        FakePrice(
            MarketData(price=Decimal(10)),
            year=YearPrices(nominal_avg=Decimal(10), closing=Decimal(10)),
        ),
        repo,
        FakeShares(),
        classes_resolver=fake_classes_resolver,
    )

    analyses = (await use_case.execute(["PETR4"])).analyses

    ttm = next(analysis for analysis in analyses if analysis.view == "ttm_live")
    year = next(
        analysis
        for analysis in analyses
        if analysis.view == "closed_year"
        and analysis.reference_date == date(2025, 12, 31)
    )
    assert ttm.reference_date == year.reference_date
    assert ttm.indicators.revenue_growth == year.indicators.revenue_growth == Decimal(1)
    assert (
        ttm.indicators.net_income_growth
        == year.indicators.net_income_growth
        == Decimal(1)
    )


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
            year=YearPrices(
                nominal_avg=Decimal(8),
                adjusted_avg=Decimal(6),
                closing=Decimal(9),
            ),
        ),
        repo,
        FakeShares(
            {
                2024: _counts(common=800, preferred=400),
                2025: _counts(common=800, preferred=400),
            }
        ),
        classes_resolver=fake_classes_resolver,
    )

    out = (await use_case.execute(["PETR4"])).analyses

    # TTM + two closed years, TTM saved first.
    assert len(out) == 3
    assert out[0].view == "ttm_live"
    views = {(a.view, a.reference_date): a for a in out}

    ttm = views[("ttm_live", date(2026, 3, 31))]
    assert ttm.price_basis == "b3_latest_close"
    assert ttm.price == Decimal(10)  # current nominal quote

    y2025 = views[("closed_year", date(2025, 12, 31))]
    assert y2025.price_basis == "b3_year_end_close"
    assert y2025.price == Decimal(9)  # B3's last close at the fiscal cut-off
    assert y2025.price_adjusted == Decimal(6)  # the total-return ruler, kept aside
    # cap = closing × shares(2025) = 9 × 1200 = 10800 (ADR 0057).
    assert y2025.indicators.company_pe == Decimal(18)
    assert y2025.indicators.company_pb == Decimal(10800) / Decimal(3600)
    # YoY vs the 2024 DFP: net income (600 - 500) / 500 = 0.2.
    assert y2025.indicators.net_income_growth == Decimal("0.2")

    # The oldest closed year has no prior DFP → growth degrades to null.
    y2024 = views[("closed_year", date(2024, 12, 31))]
    assert y2024.indicators.net_income_growth is None


async def test_closed_year_valuation_uses_the_cutoff_not_the_annual_average() -> None:
    # ADR 0057: an annual-average price times the closing share count is neither a
    # point-in-time cap nor an average cap when issuance or buybacks move the count.
    # The close drives valuation; both averages stay outside the cap.
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
            year=YearPrices(
                nominal_avg=Decimal(30),
                adjusted_avg=Decimal(10),
                closing=Decimal(32),
            ),
        ),
        repo,
        FakeShares({2024: _counts(common=60, preferred=40)}),  # 100 shares in all,
        classes_resolver=fake_classes_resolver,
    )

    await use_case.execute(["PETR4"])
    year = next(a for a in repo.saved if a.view == "closed_year")

    # cap = closing 32 × 100 = 3200. The annual averages remain separate series.
    assert year.price == Decimal(32)
    assert year.price_adjusted == Decimal(10)
    assert year.indicators.company_pe == Decimal(32)
    assert year.indicators.company_cash_yield_paid_in_period == (
        Decimal(400) / Decimal(3200)
    )
    # Preserve the characteristic that exposed the old basis mix: this filed payout
    # is below the company's nominal market value, so the computed yield must be < 1.
    assert year.indicators.company_cash_yield_paid_in_period < Decimal(1)


async def test_analyze_prices_closed_year_without_the_live_quote() -> None:
    # The live quote is missing, but the year's price is there and CVM has the
    # filed share count — the closed-year multiples must still compute, while
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
            get_error=SourceTimeoutError("quote down"),
            year=YearPrices(
                nominal_avg=Decimal(8),
                adjusted_avg=Decimal(6),
                closing=Decimal(8),
            ),
        ),
        repo,
        FakeShares({2024: _counts(common=800, preferred=400)}),
        classes_resolver=fake_classes_resolver,
    )

    await use_case.execute(["PETR4"])
    views = {(a.view, a.reference_date): a for a in repo.saved}

    y2024 = views[("closed_year", date(2024, 12, 31))]
    assert y2024.price == Decimal(8)  # the fiscal-year close, no live quote
    assert y2024.indicators.company_pe == Decimal(16)
    assert y2024.indicators.company_pb == Decimal(9600) / Decimal(3600)

    # The live view still degrades: it legitimately needs the current quote.
    ttm = views[("ttm_live", date(2026, 3, 31))]
    assert ttm.price is None
    assert ttm.indicators.company_pe is None


async def test_delisted_closed_year_names_the_price_null_non_transient() -> None:
    # The series does not carry the symbol at all (delisted, or renamed — #193):
    # the provider returns a PRICE_SYMBOL_NOT_FOUND null, and the closed-year
    # cap-multiples must carry that structural cause rather than a transient
    # MISSING_PRICE — so smaug doctor tells a delisting apart from a passing gap.
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
        FakePrice(year=YearPrices(null_reason=NullReason.PRICE_SYMBOL_NOT_FOUND)),
        repo,
        FakeShares({2024: _counts(common=800, preferred=400)}),
        classes_resolver=fake_classes_resolver,
    )

    await use_case.execute(["PETR4"])
    views = {(a.view, a.reference_date): a for a in repo.saved}

    y2024 = views[("closed_year", date(2024, 12, 31))]
    assert y2024.price is None
    assert y2024.indicators.company_pe is None
    assert y2024.indicators.null_reasons["company_pe"] is (
        NullReason.PRICE_SYMBOL_NOT_FOUND
    )


async def test_analyze_skips_when_fewer_than_four_quarters() -> None:
    two = _quarters(Sector.COMMODITY, net_income=Decimal(300))[:2]
    use_case = AnalyzePortfolioUseCase(
        FakeReader({"PETR4": two}),
        FakePrice(),
        FakeRepo(),
        FakeShares(),
        classes_resolver=fake_classes_resolver,
    )
    assert (await use_case.execute(["PETR4"])).analyses == []


async def test_analyze_skips_ticker_without_fundamentals() -> None:
    use_case = AnalyzePortfolioUseCase(
        FakeReader({}),
        FakePrice(),
        FakeRepo(),
        FakeShares(),
        classes_resolver=fake_classes_resolver,
    )
    assert (await use_case.execute(["PETR4"])).analyses == []


async def test_analyze_keeps_cpc41_eps_separate_from_closing_share_counts() -> None:
    # CVM filed 600 closing shares for 2024 and 300 for the TTM year. Those
    # counts still drive BVPS, but never substitute for CPC 41's weighted EPS
    # denominator. The closed DFP carries its own filed result.
    quarters = _quarters(
        Sector.COMMODITY, net_income=Decimal(300), equity=Decimal(6000)
    )
    annual_2024 = StandardizedFinancials(
        reference_date=date(2024, 12, 31),
        sector=Sector.COMMODITY,
        period_start=date(2024, 1, 1),
        net_income=Decimal(600),
        eps_basic=Decimal("1.125"),
        eps_diluted=Decimal("1.100"),
        equity=Decimal(3600),
    )
    repo = FakeRepo()
    use_case = AnalyzePortfolioUseCase(
        FakeReader({"PETR4": quarters}, annuals={"PETR4": [annual_2024]}),
        FakePrice(
            MarketData(price=Decimal(10)),
            year=YearPrices(
                nominal_avg=Decimal(8),
                adjusted_avg=Decimal(6),
                closing=Decimal(8),
            ),
        ),
        repo,
        FakeShares(
            {
                2024: _counts(common=400, preferred=200),
                2026: _counts(common=200, preferred=100),
            }
        ),
        classes_resolver=fake_classes_resolver,
    )

    out = (await use_case.execute(["PETR4"])).analyses
    views = {(a.view, a.reference_date): a for a in out}

    ttm = views[("ttm_live", date(2026, 3, 31))]
    assert ttm.indicators.eps is None
    assert ttm.indicators.null_reasons["eps"] is (NullReason.MISSING_CPC41_DISCLOSURE)
    assert ttm.indicators.bvps == Decimal(20)  # 6000 / 300

    y2024 = views[("closed_year", date(2024, 12, 31))]
    assert y2024.indicators.eps == Decimal("1.125")
    assert y2024.indicators.eps_diluted == Decimal("1.100")
    assert y2024.indicators.bvps == Decimal(6)  # 3600 / 600


async def test_analyze_refuses_the_quotes_own_cap_and_share_count() -> None:
    # A quote that carries its own company-wide market cap and a share count
    # derived from it breaks the identity cap ≡ price × shares for a multi-class
    # ticker: PETR4 landed +6.7% off the filed count. With no CVM filing there is
    # no honest count, so both the cap and the per-share indicators go null with a
    # named cause rather than take that biased pair (#39).
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
        FakeShares(),  # CVM filed nothing for this ticker,
        classes_resolver=fake_classes_resolver,
    )

    await use_case.execute(["PETR4"])

    ind = repo.saved[0].indicators
    assert ind.eps is None
    assert ind.company_pe is None  # the quote's own 12000 is not borrowed
    assert ind.null_reasons["eps"] is NullReason.MISSING_CPC41_DISCLOSURE
    assert ind.null_reasons["company_pe"] is NullReason.MISSING_SHARE_COUNT


async def test_analyze_keeps_bvps_when_price_is_missing() -> None:
    # BVPS needs only the closing share count. TTM EPS independently remains
    # unavailable because four class disclosures cannot be added.
    repo = FakeRepo()
    use_case = AnalyzePortfolioUseCase(
        FakeReader(
            {
                "BBAS3": _quarters(
                    Sector.BANK, net_income=Decimal(200), equity=Decimal(8000)
                )
            }
        ),
        FakePrice(error=SourceForbiddenError("403")),
        repo,
        FakeShares({2026: _counts(common=400)}),  # BBAS3 lists ON only,
        classes_resolver=fake_classes_resolver,
    )

    await use_case.execute(["BBAS3"])

    saved = repo.saved[0]
    assert saved.indicators.eps is None
    assert saved.indicators.null_reasons["eps"] is (NullReason.MISSING_CPC41_DISCLOSURE)
    assert saved.indicators.bvps == Decimal(20)  # 8000 / 400
    assert saved.indicators.pe_basic is None  # still no price and no TTM CPC 41 EPS
    assert saved.indicators.null_reasons["pe_basic"] is (
        NullReason.MISSING_CPC41_DISCLOSURE
    )
    assert saved.indicators.company_pe is None
    assert saved.indicators.null_reasons["company_pe"] is (
        NullReason.PRICE_SOURCE_UNAVAILABLE
    )


async def test_analyze_degrades_when_price_unavailable() -> None:
    use_case = AnalyzePortfolioUseCase(
        FakeReader(
            {
                "BBAS3": _quarters(
                    Sector.BANK, net_income=Decimal(200), equity=Decimal(8000)
                )
            }
        ),
        FakePrice(error=SourceForbiddenError("403")),
        FakeRepo(),
        FakeShares(),
        classes_resolver=fake_classes_resolver,
    )

    out = (await use_case.execute(["BBAS3"])).analyses

    assert len(out) == 1
    assert out[0].indicators.roe == Decimal("0.1")  # 800 / 8000, fundamentals survive
    assert out[0].indicators.company_pe is None  # no price -> no market multiple
    assert out[0].price is None


async def test_analyze_degrades_when_price_times_out() -> None:
    # A transport timeout is a SourceError, so it degrades like a plan-gate 403:
    # market multiples go null, accounting indicators survive.
    use_case = AnalyzePortfolioUseCase(
        FakeReader(
            {
                "BBAS3": _quarters(
                    Sector.BANK, net_income=Decimal(200), equity=Decimal(8000)
                )
            }
        ),
        FakePrice(error=SourceTimeoutError("read timed out")),
        FakeRepo(),
        FakeShares({2026: _counts(common=400)}),
        classes_resolver=fake_classes_resolver,
    )

    out = (await use_case.execute(["BBAS3"])).analyses

    assert len(out) == 1
    assert out[0].indicators.roe == Decimal("0.1")  # fundamentals survive
    assert out[0].indicators.company_pe is None  # timeout -> no market multiple
    assert out[0].indicators.null_reasons["company_pe"] is (
        NullReason.PRICE_SOURCE_TIMEOUT
    )
    assert out[0].price is None


async def test_analyze_names_a_malformed_price_source() -> None:
    use_case = AnalyzePortfolioUseCase(
        FakeReader(
            {
                "BBAS3": _quarters(
                    Sector.BANK, net_income=Decimal(200), equity=Decimal(8000)
                )
            }
        ),
        FakePrice(error=SourceMalformedError("invalid COTAHIST")),
        FakeRepo(),
        FakeShares({2026: _counts(common=400)}),
        classes_resolver=fake_classes_resolver,
    )

    out = (await use_case.execute(["BBAS3"])).analyses

    assert out[0].indicators.null_reasons["company_pe"] is (
        NullReason.PRICE_SOURCE_MALFORMED
    )


async def test_analyze_keeps_a_sibling_symbol_not_found_reason_on_the_cap() -> None:
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
                "PETR3": MarketData(
                    price_null_reason=NullReason.PRICE_SYMBOL_NOT_FOUND
                ),
                "PETR4": MarketData(price=Decimal(10)),
            }
        ),
        repo,
        FakeShares({2026: _counts(common=800, preferred=400)}),
        classes_resolver=fake_classes_resolver,
    )

    await use_case.execute(["PETR4"])

    saved = repo.saved[0]
    assert saved.indicators.company_pe is None
    assert saved.indicators.null_reasons["company_pe"] is (
        NullReason.PRICE_SYMBOL_NOT_FOUND
    )


async def test_a_year_before_the_tickers_first_trade_gets_no_row() -> None:
    # ADR 0048 (reopening #153): CXSE3 listed 2021-04-29, so 2020 — a fiscal year
    # CVM filed before its own first B3 session — is not a row this analysis
    # produces at all, not even one with a correctly-named null price. The
    # evidence is B3's own tape walked forward from the candidate year, never the
    # FCA: CXSE3 gets a price from 2021 on, none in 2020.
    annual_2020 = StandardizedFinancials(
        reference_date=date(2020, 12, 31),
        sector=Sector.INSURER,
        period_start=date(2020, 1, 1),
        net_income=Decimal(600),
        equity=Decimal(3600),
    )
    annual_2021 = StandardizedFinancials(
        reference_date=date(2021, 12, 31),
        sector=Sector.INSURER,
        period_start=date(2021, 1, 1),
        net_income=Decimal(700),
        equity=Decimal(4000),
    )
    repo = FakeRepo()
    use_case = AnalyzePortfolioUseCase(
        FakeReader({"CXSE3": []}, annuals={"CXSE3": [annual_2020, annual_2021]}),
        FakePrice(
            year_by_symbol_and_year={
                ("CXSE3", 2021): YearPrices(
                    nominal_avg=Decimal(12), closing=Decimal(12)
                ),
            },
        ),
        repo,
        FakeShares({2020: _counts(common=800), 2021: _counts(common=800)}),
        classes_resolver=fake_classes_resolver,
    )

    await use_case.execute(["CXSE3"])

    closed_years = {
        a.reference_date.year for a in repo.saved if a.view == "closed_year"
    }
    assert closed_years == {2021}  # 2020 produced no row at all


async def test_only_pre_trading_filings_have_a_named_no_analysis_outcome() -> None:
    annual = StandardizedFinancials(
        reference_date=date(2020, 12, 31),
        sector=Sector.INSURER,
        period_start=date(2020, 1, 1),
        net_income=Decimal(600),
        equity=Decimal(3600),
    )
    use_case = AnalyzePortfolioUseCase(
        FakeReader({"CXSE3": []}, annuals={"CXSE3": [annual]}),
        FakePrice(
            year_by_symbol_and_year={
                ("CXSE3", 2021): YearPrices(
                    nominal_avg=Decimal(12), closing=Decimal(12)
                ),
            },
        ),
        FakeRepo(),
        FakeShares({2020: _counts(common=800)}),
        classes_resolver=fake_classes_resolver,
    )

    outcome = (await use_case.execute(["CXSE3"])).outcomes[0]

    assert outcome.status is AnalysisStatus.SKIPPED
    assert outcome.no_analysis_reason is (
        NoAnalysisReason.ALL_EXERCISES_PRE_FIRST_B3_SESSION
    )
    assert "all 1 closed exercise(s)" in outcome.detail
    assert "no complete four-quarter TTM window" in outcome.detail


async def test_sparse_b3_year_keeps_its_accounting_analysis_row() -> None:
    class SparseSessionPrice(FakePrice):
        async def year_sessions(
            self, ticker: str, year: int
        ) -> tuple[SessionClose, ...]:
            if ticker == "PETR4" and year == 2025:
                return (SessionClose(date(2025, 12, 3), Decimal(10)),)
            return ()

    annual = StandardizedFinancials(
        reference_date=date(2025, 12, 31),
        sector=Sector.INDUSTRY,
        period_start=date(2025, 1, 1),
        revenue=Decimal(1000),
        net_income=Decimal(100),
        equity=Decimal(600),
    )
    use_case = AnalyzePortfolioUseCase(
        FakeReader({"PETR4": []}, annuals={"PETR4": [annual]}),
        SparseSessionPrice(
            year_by_symbol_and_year={
                ("PETR4", 2025): YearPrices(
                    null_reason=NullReason.PRICE_SYMBOL_NOT_FOUND
                ),
                ("PETR4", 2026): YearPrices(
                    nominal_avg=Decimal(12), closing=Decimal(12)
                ),
            },
        ),
        FakeRepo(),
        FakeShares({2025: _counts(common=800)}),
        classes_resolver=fake_classes_resolver,
    )

    outcome = (await use_case.execute(["PETR4"])).outcomes[0]

    assert outcome.status is AnalysisStatus.ANALYZED
    assert len(outcome.analyses) == 1
    assert outcome.analyses[0].reference_date == date(2025, 12, 31)
    assert outcome.analyses[0].indicators.net_margin == Decimal("0.1")
    assert outcome.analyses[0].indicators.company_pe is None
    assert outcome.analyses[0].indicators.null_reasons["company_pe"] is (
        NullReason.PRICE_SYMBOL_NOT_FOUND
    )


async def test_unresolved_b3_identity_has_a_named_no_analysis_outcome() -> None:
    annual = StandardizedFinancials(
        reference_date=date(2020, 12, 31),
        sector=Sector.INDUSTRY,
        period_start=date(2020, 1, 1),
        net_income=Decimal(600),
        equity=Decimal(3600),
    )
    use_case = AnalyzePortfolioUseCase(
        FakeReader({"PETR4": []}, annuals={"PETR4": [annual]}),
        FakePrice(
            year_by_symbol_and_year={
                ("PETR4", 2020): YearPrices(
                    null_reason=NullReason.PRICE_SYMBOL_NOT_FOUND
                ),
                ("PETR4", 2021): YearPrices(
                    nominal_avg=Decimal(12), closing=Decimal(12)
                ),
            },
        ),
        FakeRepo(),
        FakeShares({2020: _counts(common=800)}),
        classes_resolver=fake_classes_resolver,
    )

    outcome = (await use_case.execute(["PETR4"])).outcomes[0]

    assert outcome.status is AnalysisStatus.SKIPPED
    assert outcome.no_analysis_reason is NoAnalysisReason.UNRESOLVED_SECURITY_IDENTITY
    assert "code chain cannot name" in outcome.detail


async def test_a_priced_ticker_with_a_vendor_gap_stays_a_transient_miss() -> None:
    # A ticker the tape never prices, in any direction, is left alone rather than
    # guessed at: there is no later year to prove it "had not started yet", so the
    # row survives and keeps the ordinary transient cause.
    annual = StandardizedFinancials(
        reference_date=date(2015, 12, 31),
        sector=Sector.UTILITY,
        period_start=date(2015, 1, 1),
        net_income=Decimal(600),
        equity=Decimal(3600),
    )
    repo = FakeRepo()
    use_case = AnalyzePortfolioUseCase(
        FakeReader({"TAEE11": []}, annuals={"TAEE11": [annual]}),
        FakePrice(year=YearPrices()),
        repo,
        FakeShares({2015: _counts(common=800)}),
        classes_resolver=fake_classes_resolver,
    )

    await use_case.execute(["TAEE11"])

    closed = [a for a in repo.saved if a.reference_date == date(2015, 12, 31)]
    assert closed, "an unresolved gap keeps its row rather than being suppressed"
    assert closed[0].indicators.null_reasons["company_pe"] is not (
        NullReason.NOT_YET_LISTED
    )


async def test_a_sibling_class_not_yet_traded_is_named_not_yet_listed() -> None:
    # The real #164 shape: TAEE11 (the unit) trades fine, but its components
    # TAEE3/TAEE4 print no B3 session until 2017 — nearly every share moves
    # bundled in the unit until enough free float trades loose. The FCA's
    # Data_Inicio_Listagem cannot tell this apart from an ordinary listing date
    # (it reads 2006 for TAEE4 too, same as TAEE11), so the cause has to come
    # from B3's own tape: TAEE3/TAEE4 get a price from 2017 on, none in 2015.
    annual = StandardizedFinancials(
        reference_date=date(2015, 12, 31),
        sector=Sector.UTILITY,
        period_start=date(2015, 1, 1),
        net_income=Decimal(600),
        equity=Decimal(3600),
    )
    repo = FakeRepo()
    use_case = AnalyzePortfolioUseCase(
        FakeReader({"TAEE11": []}, annuals={"TAEE11": [annual]}),
        FakePrice(
            year_by_symbol_and_year={
                ("TAEE11", 2015): YearPrices(
                    nominal_avg=Decimal(22), closing=Decimal(22)
                ),
                ("TAEE3", 2017): YearPrices(nominal_avg=Decimal(8), closing=Decimal(8)),
                ("TAEE4", 2017): YearPrices(nominal_avg=Decimal(7), closing=Decimal(7)),
            },
        ),
        repo,
        FakeShares({2015: _counts(common=800, preferred=1600)}),
        classes_resolver=fake_classes_resolver,
    )

    await use_case.execute(["TAEE11"])

    closed = [a for a in repo.saved if a.reference_date == date(2015, 12, 31)]
    assert closed, "the closed year should still be persisted"
    assert closed[0].price == Decimal(22)  # TAEE11 itself was trading fine
    assert closed[0].indicators.market_cap is None
    reasons = closed[0].indicators.null_reasons
    assert reasons["market_cap"] is NullReason.NOT_YET_LISTED
    assert reasons["company_pe"] is NullReason.NOT_YET_LISTED


class _ExplodingReader:
    """A reader that fails for one ticker and answers normally for the rest."""

    def __init__(self, working: dict[str, list[StandardizedFinancials]]) -> None:
        self._working = working

    async def history(self, ticker: str) -> list[StandardizedFinancials]:
        if ticker == "BOOM3":
            raise ValueError("malformed payload")
        return self._working.get(ticker, [])

    async def annuals(self, ticker: str) -> list[StandardizedFinancials]:
        if ticker == "BOOM3":
            raise ValueError("malformed payload")
        return []

    async def annual(self, ticker: str) -> StandardizedFinancials | None:
        return None


async def test_one_ticker_failing_does_not_end_the_run() -> None:
    # One malformed payload in an exchange-wide run must not discard every ticker
    # that had nothing wrong with it.
    repo = FakeRepo()
    use_case = AnalyzePortfolioUseCase(
        _ExplodingReader(
            {
                "PETR4": _quarters(
                    Sector.COMMODITY, net_income=Decimal(300), equity=Decimal(6000)
                )
            }
        ),
        FakePrice(MarketData(price=Decimal(10))),
        repo,
        FakeShares({2026: _counts(common=800, preferred=400)}),
        classes_resolver=fake_classes_resolver,
    )

    run = await use_case.execute(["BOOM3", "PETR4"])

    assert [(o.ticker, o.status) for o in run.outcomes] == [
        ("BOOM3", AnalysisStatus.ERROR),
        ("PETR4", AnalysisStatus.ANALYZED),
    ]
    assert "malformed payload" in run.failed[0].detail
    assert [a.ticker for a in run.analyses] == ["PETR4"]  # the good one still landed


async def test_a_ticker_with_nothing_mirrored_is_skipped_not_failed() -> None:
    """No filings is a fact about the company, not a fault of the run."""
    use_case = AnalyzePortfolioUseCase(
        _ExplodingReader({}),
        FakePrice(MarketData(price=Decimal(10))),
        FakeRepo(),
        FakeShares({}),
        classes_resolver=fake_classes_resolver,
    )

    run = await use_case.execute(["NADA3"])

    assert run.outcomes[0].status == AnalysisStatus.SKIPPED
    assert (
        run.outcomes[0].no_analysis_reason is NoAnalysisReason.NO_MIRRORED_FUNDAMENTALS
    )
    assert run.outcomes[0].detail == "no CVM fundamentals are mirrored"
    assert run.failed == ()
