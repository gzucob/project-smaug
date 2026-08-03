"""MongoSharesReader: per-year share counts from the CAPITAL raw mirror."""

from collections.abc import Sequence
from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Any

from smaug.analysis.domain.capital import BaseChange, RestatementStep, factor_at
from smaug.analysis.domain.financials import ShareCounts
from smaug.analysis.infrastructure.mongo_capital import MongoSharesReader


class FakeCursor:
    def __init__(self, documents: list[dict[str, Any]]) -> None:
        self._documents = documents

    def sort(self, key: str, direction: int) -> "FakeCursor":
        self._documents.sort(key=lambda d: d[key], reverse=direction < 0)
        return self

    async def __aiter__(self) -> Any:
        for document in self._documents:
            yield document


class FakeCollection:
    def __init__(self, documents: list[dict[str, Any]]) -> None:
        self._documents = documents

    def find(self, query: dict[str, Any], /) -> FakeCursor:
        matched = [
            d for d in self._documents if all(d.get(k) == v for k, v in query.items())
        ]
        return FakeCursor(matched)


def _doc(
    ticker: str,
    year: int,
    total: int,
    *,
    common: int | None = None,
    preferred: int = 0,
    fetched_at: datetime | None = None,
    version: int = 1,
    approved: str | None = "2023-04-27",
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "reference_date": f"{year}-12-31",
        "version": version,
        # The FRE writes an absent class as 0, never as a blank.
        "common_shares": total if common is None else common,
        "preferred_shares": preferred,
        "total_shares": total,
    }
    # ``approved=None`` is a document mirrored before #86, when the approval date
    # was not stored at all — the append-only mirror still holds those.
    if approved is not None:
        payload["approval_date"] = approved
    return {
        "ticker": ticker,
        "source": "cvm",
        "module": "CAPITAL",
        "fetched_at": fetched_at or datetime(2026, 1, 1, tzinfo=UTC),
        "payload": payload,
    }


def _composition(
    ticker: str,
    reference_date: str,
    issued_total: int,
    *,
    common: int = 0,
    preferred: int = 0,
    total: int | None = None,
    version: int = 1,
) -> dict[str, Any]:
    """A DFP/ITR ``composicao_capital`` row — the only filing that names treasury."""
    return {
        "ticker": ticker,
        "source": "cvm",
        "module": "CAPITAL_DFP",
        "fetched_at": datetime(2026, 1, 1, tzinfo=UTC),
        "payload": {
            "reference_date": reference_date,
            "version": version,
            "total_shares": issued_total,
            "treasury_common_shares": common,
            "treasury_preferred_shares": preferred,
            "treasury_total_shares": total if total is not None else common + preferred,
        },
    }


async def test_counts_are_served_net_of_the_shares_held_in_treasury() -> None:
    # BBSE3 2024: 58.8 M of its 2 bn shares are its own (ADR 0017). Pricing them
    # over-values the company by 3%, in one direction, every year.
    reader = MongoSharesReader(
        FakeCollection(
            [
                _doc("BBSE3", 2024, 2_000_000_000),
                _composition("BBSE3", "2024-12-31", 2_000_000_000, common=58_813_981),
            ]
        )
    )

    assert await reader.outstanding("BBSE3", 2024) == Decimal(1_941_186_019)


async def test_the_latest_period_of_the_year_carries_the_treasury_balance() -> None:
    # The mirror holds the DFP's year-end row *and* the ITR quarters (ADR 0016). For a
    # closed year the balance that stands is 31-Dec's, not September's.
    reader = MongoSharesReader(
        FakeCollection(
            [
                _doc("WEGE3", 2024, 4_197_317_998),
                _composition("WEGE3", "2024-12-31", 4_197_317_998, common=1_780_620),
                _composition("WEGE3", "2024-09-30", 4_197_317_998, common=9_999_999),
            ]
        )
    )

    assert await reader.outstanding("WEGE3", 2024) == Decimal(4_195_537_378)


async def test_a_negative_treasury_composition_nets_by_its_magnitude() -> None:
    # BBDC4 files a negative treasury count for 2022 (#88). The magnitude is the
    # balance and the sign is noise (the DMPL cost trail corroborates it — see
    # ``outstanding_counts``), so the reader nets it like any other treasury:
    # 10,658,488,028 issued − |16,318| thousand = 10,642,170,028 outstanding.
    reader = MongoSharesReader(
        FakeCollection(
            [
                _doc(
                    "BBDC4",
                    2022,
                    10_658_488_028,
                    common=5_338_393_881,
                    preferred=5_320_094_147,
                ),
                _composition(
                    "BBDC4", "2022-12-31", 10_658_488, common=-8_089, preferred=-8_229
                ),
            ]
        )
    )

    assert await reader.outstanding("BBDC4", 2022) == Decimal(10_642_170_028)


async def test_the_highest_filed_version_supersedes_the_rest() -> None:
    # The mirror now holds every FRE amendment (ADR 0016), and the FRE is heavily
    # amended. The amendment wins on its *version*, not on when it was ingested —
    # ordering by fetched_at alone would make the answer depend on ingestion order.
    reader = MongoSharesReader(
        FakeCollection(
            [
                _doc(
                    "PETR4",
                    2024,
                    200,
                    version=27,
                    fetched_at=datetime(2026, 1, 1, tzinfo=UTC),
                ),
                _doc(
                    "PETR4",
                    2024,
                    100,
                    version=3,
                    fetched_at=datetime(2026, 6, 1, tzinfo=UTC),  # ingested later
                ),
            ]
        )
    )

    assert await reader.outstanding("PETR4", 2024) == Decimal(
        200
    )  # v27, not the late v3


async def test_outstanding_returns_the_count_filed_for_that_year() -> None:
    reader = MongoSharesReader(
        FakeCollection([_doc("PETR4", 2024, 13_000), _doc("PETR4", 2025, 12_888)])
    )

    assert await reader.outstanding("PETR4", 2025) == Decimal(12_888)
    assert await reader.outstanding("PETR4", 2024) == Decimal(13_000)


async def test_outstanding_falls_back_to_the_nearest_earlier_year() -> None:
    # 2026 was never ingested; the 2025 filing is the closest thing on file.
    reader = MongoSharesReader(FakeCollection([_doc("PETR4", 2025, 12_888)]))

    assert await reader.outstanding("PETR4", 2026) == Decimal(12_888)


async def test_outstanding_is_none_before_the_earliest_filing() -> None:
    reader = MongoSharesReader(FakeCollection([_doc("PETR4", 2025, 12_888)]))

    assert await reader.outstanding("PETR4", 2021) is None


async def test_outstanding_is_the_unit_count_for_a_unit_ticker() -> None:
    # A unit bundles 1 ON + 2 PN, so the divisor for its per-unit LPA/VPA is the
    # number of units — the filed share count over three (#38).
    reader = MongoSharesReader(FakeCollection([_doc("TAEE11", 2025, 1_033_496_721)]))

    assert await reader.outstanding("TAEE11", 2025) == Decimal(344_498_907)


async def test_counts_split_the_filing_by_share_class() -> None:
    reader = MongoSharesReader(
        FakeCollection([_doc("PETR4", 2025, 13_044, common=7_442, preferred=5_602)])
    )

    assert await reader.counts("PETR4", 2025) == ShareCounts(
        common=Decimal(7_442),
        preferred=Decimal(5_602),
        total=Decimal(13_044),
    )


async def test_counts_are_served_for_a_unit_ticker() -> None:
    # The opposite of ``outstanding``: the multi-class cap prices the *underlying*
    # classes, which is exactly what a unit's bundle quote cannot give (ADR 0014).
    reader = MongoSharesReader(
        FakeCollection(
            [_doc("TAEE11", 2025, 1_033_496, common=590_712, preferred=442_784)]
        )
    )

    filed = await reader.counts("TAEE11", 2025)

    assert filed is not None
    assert filed.common == Decimal(590_712)
    assert filed.preferred == Decimal(442_784)


async def test_a_class_filed_as_zero_is_absent_not_zero() -> None:
    # A single-class filer writes 0 preferred shares; zero shares is a gap, never
    # a denominator — the cap must not multiply a price by it.
    reader = MongoSharesReader(
        FakeCollection([_doc("WEGE3", 2025, 4_200, common=4_200, preferred=0)])
    )

    filed = await reader.counts("WEGE3", 2025)

    assert filed is not None
    assert filed.preferred is None


async def test_a_zero_total_is_read_from_the_class_lines_instead() -> None:
    # BBAS3's 2023 FRE files 5.73 bn ordinary shares and then writes 0 in the total
    # column (#39). Dropping the filing over that blank sent 2023 back to 2022's
    # 2.87 bn — half the company, right across the 2:1 bonus. The class line is the
    # filing's own number, so it stands in.
    reader = MongoSharesReader(
        FakeCollection(
            [
                _doc("BBAS3", 2022, 2_865, common=2_865),
                _doc("BBAS3", 2023, 0, common=5_730),
            ]
        )
    )

    assert await reader.outstanding("BBAS3", 2023) == Decimal(5_730)


async def test_a_filing_with_no_usable_count_falls_back_to_the_prior_year() -> None:
    reader = MongoSharesReader(
        FakeCollection(
            [
                _doc("BBAS3", 2022, 2_865, common=2_865),
                _doc("BBAS3", 2023, 0, common=0),
            ]
        )
    )

    assert await reader.outstanding("BBAS3", 2023) == Decimal(2_865)


async def test_outstanding_prefers_the_later_ingestion_of_the_same_year() -> None:
    reader = MongoSharesReader(
        FakeCollection(
            [
                _doc("PETR4", 2025, 111, fetched_at=datetime(2026, 1, 1, tzinfo=UTC)),
                _doc("PETR4", 2025, 222, fetched_at=datetime(2026, 6, 1, tzinfo=UTC)),
            ]
        )
    )

    assert await reader.outstanding("PETR4", 2025) == Decimal(222)


async def test_the_latest_approval_of_a_version_is_the_companys_capital() -> None:
    # A single FRE version restates the whole capital history, several rows of it
    # paid-in: SANEPAR's 2021 filing carries the 2020 split (1.51 bn shares) and the
    # 2016 approvals it superseded (503 M). Picking by cursor order took the 2016
    # row and priced the company at a third of its size (#86).
    reader = MongoSharesReader(
        FakeCollection(
            [
                _doc(
                    "SAPR11",
                    2021,
                    1_511_205_519,
                    common=503_735_259,
                    preferred=1_007_470_260,
                    approved="2020-03-27",
                    fetched_at=datetime(2026, 1, 1, tzinfo=UTC),
                ),
                _doc(
                    "SAPR11",
                    2021,
                    503_735_173,
                    common=167_911_724,
                    preferred=335_823_449,
                    approved="2016-12-19",
                    fetched_at=datetime(2026, 6, 1, tzinfo=UTC),
                ),
            ]
        )
    )

    assert await reader.counts("SAPR11", 2021) == ShareCounts(
        common=Decimal(503_735_259),
        preferred=Decimal(1_007_470_260),
        total=Decimal(1_511_205_519),
    )


async def test_an_undated_legacy_row_never_beats_a_dated_one() -> None:
    # The mirror is append-only: the documents stored before #86 carry no approval
    # date and are still there, ingested *after* nothing. Ranking them below every
    # dated row is what keeps a stale copy from winning on ingestion order alone.
    reader = MongoSharesReader(
        FakeCollection(
            [
                _doc(
                    "BBAS3",
                    2022,
                    2_865_417_020,
                    approved="2023-04-27",
                    fetched_at=datetime(2026, 1, 1, tzinfo=UTC),
                ),
                _doc(
                    "BBAS3",
                    2022,
                    286_541_720,
                    approved=None,
                    fetched_at=datetime(2026, 6, 1, tzinfo=UTC),
                ),
            ]
        )
    )

    assert await reader.outstanding("BBAS3", 2022) == Decimal(2_865_417_020)


async def test_outstanding_is_none_without_any_capital_document() -> None:
    reader = MongoSharesReader(FakeCollection([]))

    assert await reader.outstanding("PETR4", 2025) is None


async def test_a_pre_bonus_year_is_served_on_the_current_base() -> None:
    # BBAS3's 2023 2:1 bonus (ADR 0027): the 2022 closed year serves 5.73 bn,
    # not the 2.87 bn filed — the price side divides the 2022 closes by the same
    # bonus (ADR 0033), and the count has to sit on the base of the price it
    # multiplies.
    reader = MongoSharesReader(
        FakeCollection(
            [
                _doc("BBAS3", 2022, 2_865_417_020),
                _doc("BBAS3", 2023, 5_730_834_040),
            ]
        )
    )

    assert await reader.outstanding("BBAS3", 2022) == Decimal(5_730_834_040)
    counts = await reader.counts("BBAS3", 2022)
    assert counts is not None
    assert counts.common == Decimal(5_730_834_040)


async def test_the_restatement_is_published_dated_for_the_price_to_divide_by() -> None:
    # The counts are multiplied by it; an as-traded price has to be divided by the
    # same number or the cap moves. B3 publishes the price as traded, so this is
    # what pairs the two (ADR 0032). BBAS3 2022 measured 2x against the adjusted
    # vendor series in all ten years.
    reader = MongoSharesReader(
        FakeCollection(
            [
                _doc("BBAS3", 2022, 2_865_417_020),
                _doc("BBAS3", 2023, 5_730_834_040),
            ]
        )
    )

    timeline = await reader.restatement_timeline("BBAS3")

    assert [(step.effective, step.ratio) for step in timeline] == [
        # Nothing dates an inferred move, so it takes the first day of the filing
        # year that reported it — where the per-year factor already placed it.
        (date(2023, 1, 1), Decimal(2))
    ]
    # Which is the yearly reading it refines: 2022 divides by 2, 2023 by nothing.
    assert factor_at(timeline, date(2022, 12, 31)) == Decimal(2)
    assert factor_at(timeline, date(2023, 6, 30)) == Decimal(1)


def _b3_event(
    ticker: str,
    *,
    kind: str,
    factor: str,
    approved: str,
    last_prior: str,
    isin: str = "BRBBASACNOR3",
) -> dict[str, Any]:
    """A ``GetListedSupplementCompany.stockDividends`` row, as B3 publishes it."""
    return {
        "ticker": ticker,
        "source": "cvm",  # the run's source; the module says where the row is from
        "module": "CAPITAL_EVENT_B3",
        "fetched_at": datetime(2026, 1, 1, tzinfo=UTC),
        "payload": {
            "event_type": kind,
            "factor": factor,
            "approval_date": approved,
            "last_date_prior": last_prior,
            "isin_code": isin,
        },
    }


async def test_the_exchange_dates_the_step_the_filing_year_could_only_place() -> None:
    # BBAS3: the FRE reports the doubled count in its 2023 filing and declares
    # nothing, but the split traded on 2024-04-16 (ADR 0034).
    reader = MongoSharesReader(
        FakeCollection(
            [
                _doc("BBAS3", 2022, 2_865_417_020),
                _doc("BBAS3", 2023, 5_730_834_040),
                _b3_event(
                    "BBAS3",
                    kind="DESDOBRAMENTO",
                    factor="100,00000000000",
                    approved="02/02/2024",
                    last_prior="15/04/2024",
                ),
            ]
        )
    )

    timeline = await reader.restatement_timeline("BBAS3")

    # A DESDOBRAMENTO's factor is the *percentage* of new shares: 100 is a 2:1
    # split. And the new base starts the session after the last one on the old.
    assert timeline == (RestatementStep(date(2024, 4, 16), Decimal(2)),)
    assert factor_at(timeline, date(2023, 6, 30)) == 2


async def test_a_grupamentos_factor_is_the_multiple_and_not_a_percentage() -> None:
    # MGLU3's 1:10 grupamento files 0,10 — read as a percentage it would be a
    # 1.001x event, and the 2024 price would come out ten times too low.
    reader = MongoSharesReader(
        FakeCollection(
            [
                _doc("MGLU3", 2023, 7_389_952_489),
                _doc("MGLU3", 2024, 738_995_248),
                _b3_event(
                    "MGLU3",
                    kind="GRUPAMENTO",
                    factor="0,10000000000",
                    approved="24/04/2024",
                    last_prior="24/05/2024",
                ),
            ]
        )
    )

    timeline = await reader.restatement_timeline("MGLU3")

    assert timeline == (RestatementStep(date(2024, 5, 25), Decimal("0.1")),)


async def test_one_event_listed_once_per_isin_is_still_one_event() -> None:
    # B3 repeats a row for every asset code the company has had. Counted three
    # times, BBAS3's 2:1 split would compound to 8x.
    rows = [
        _b3_event(
            "BBAS3",
            kind="DESDOBRAMENTO",
            factor="100,00000000000",
            approved="02/02/2024",
            last_prior="15/04/2024",
            isin=isin,
        )
        for isin in ("BRBBASACNOR3", "BRBBASA04OR8", "BRBBASA05OR5")
    ]
    reader = MongoSharesReader(
        FakeCollection(
            [
                _doc("BBAS3", 2022, 2_865_417_020),
                _doc("BBAS3", 2023, 5_730_834_040),
                *rows,
            ]
        )
    )

    assert await reader.restatement_timeline("BBAS3") == (
        RestatementStep(date(2024, 4, 16), Decimal(2)),
    )


async def test_a_spin_off_carries_a_factor_and_is_not_a_restatement() -> None:
    # ``CIS RED CAP`` hands shareholders stock in a *different* company. Its
    # factor of 100 would read as a 2:1 split of the one being restated.
    reader = MongoSharesReader(
        FakeCollection(
            [
                _doc("BBDC4", 2022, 1_000_000),
                _doc("BBDC4", 2023, 2_000_000),
                _b3_event(
                    "BBDC4",
                    kind="CIS RED CAP",
                    factor="100,00000000000",
                    approved="30/03/2023",
                    last_prior="09/08/2023",
                ),
            ]
        )
    )

    timeline = await reader.restatement_timeline("BBDC4")

    # The step stands on the filing year, untouched by a row that is not one.
    assert timeline == (RestatementStep(date(2023, 1, 1), Decimal(2)),)


async def test_the_restatement_is_empty_without_any_capital_document() -> None:
    # No filing is not "no corporate action" — but an empty timeline leaves the
    # price exactly as the exchange published it, which is the honest default.
    reader = MongoSharesReader(FakeCollection([]))

    assert await reader.restatement_timeline("PETR4") == ()


async def test_the_current_year_is_its_own_base() -> None:
    reader = MongoSharesReader(
        FakeCollection(
            [
                _doc("BBAS3", 2022, 2_865_417_020),
                _doc("BBAS3", 2023, 5_730_834_040),
            ]
        )
    )

    assert await reader.outstanding("BBAS3", 2023) == Decimal(5_730_834_040)


async def test_treasury_is_netted_before_the_restatement_factor() -> None:
    # The composition is filed at its year's own base: net first, restate after.
    reader = MongoSharesReader(
        FakeCollection(
            [
                _doc("ACME3", 2022, 1_000_000),
                _doc("ACME3", 2023, 2_000_000),
                _composition("ACME3", "2022-12-31", 1_000_000, common=100_000),
            ]
        )
    )

    # (1,000,000 issued − 100,000 treasury) × 2 — never (1,000,000 × 2 − 100,000).
    assert await reader.outstanding("ACME3", 2022) == Decimal(1_800_000)


class FakeBaseChanges:
    """A ``BaseChangeReader`` that records the years it was asked for."""

    def __init__(self, changes: list[BaseChange]) -> None:
        self._changes = changes
        self.asked: tuple[int, ...] = ()

    async def base_changes(
        self, ticker: str, years: Sequence[int]
    ) -> Sequence[BaseChange]:
        self.asked = tuple(years)
        return self._changes


async def test_the_tape_dates_the_move_the_filing_only_places_in_a_year() -> None:
    # BBAS3's split traded on 16 April 2024; the 2023 FRE is what first counts it,
    # so the chain parked it on 2023-01-01 and restated the wrong twelve months.
    changes = FakeBaseChanges(
        [BaseChange(session=date(2024, 4, 16), ratio=Decimal("2.0229"))]
    )
    reader = MongoSharesReader(
        FakeCollection(
            [
                _doc("BBAS3", 2022, 2_865_417_020),
                _doc("BBAS3", 2023, 5_730_834_040),
            ]
        ),
        base_changes=changes,
    )

    timeline = await reader.restatement_timeline("BBAS3")

    assert [(step.effective, step.ratio) for step in timeline] == [
        (date(2024, 4, 16), Decimal(2))
    ]
    assert factor_at(timeline, date(2024, 4, 15)) == Decimal(2)
    assert factor_at(timeline, date(2024, 4, 16)) == Decimal(1)
    # One year past the last filed one, because the FRE reports an action late:
    # a span stopping at 2023 would never be offered April 2024.
    assert changes.asked == (2022, 2023, 2024)
