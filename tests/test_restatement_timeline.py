"""The restatement resolved to dates, which is what a daily price series needs.

``restatement_factors`` answers per filing year, because a count series has one
value a year. A price has one a session, and a corporate action lands on a day —
so the same chain is republished here with the date each move happened on
(ADR 0033). Every fixture is a real filing.
"""

from datetime import date
from decimal import Decimal

from smaug.analysis.domain.capital import (
    CorporateAction,
    ExchangeAction,
    RestatementStep,
    average_factor,
    factor_at,
    restatement_factors,
    restatement_timeline,
)
from smaug.analysis.domain.financials import SessionClose


def _action(kind: str, before: int, after: int, approved: str) -> CorporateAction:
    return CorporateAction(
        approval_date=approved,
        kind=kind,
        total_before=Decimal(before),
        total_after=Decimal(after),
    )


def _close(day: str, price: str) -> SessionClose:
    return SessionClose(session=date.fromisoformat(day), close=Decimal(price))


def test_a_declared_action_is_dated_by_its_own_approval() -> None:
    # BBAS3's bonus: approved 25 April 2024, and the FRE that reports the doubled
    # count is the 2024 one. The yearly factor can only say "some time in 2024";
    # the declaration says which day, and the year's own sessions divide.
    filed = {2023: Decimal(2_865_417_020), 2024: Decimal(5_730_834_040)}
    declared = [_action("Bonificação", 2_865_417_020, 5_730_834_040, "2024-04-25")]

    timeline = restatement_timeline(filed, actions=declared)

    assert timeline == (RestatementStep(date(2024, 4, 25), Decimal(2)),)
    assert factor_at(timeline, date(2024, 4, 24)) == 2
    assert factor_at(timeline, date(2024, 4, 25)) == 1


def test_an_inferred_move_takes_the_first_day_of_the_year_that_reported_it() -> None:
    """Where the per-year factor already put it — so inference is unchanged.

    Nothing dates a move read off two counts: all it says is that by the next
    filing the base had moved. Any date inside the reporting year would restate
    part of that year's sessions on a guess.
    """
    filed = {2023: Decimal(2_865_417_020), 2024: Decimal(5_730_834_040)}

    timeline = restatement_timeline(filed)

    assert timeline == (RestatementStep(date(2024, 1, 1), Decimal(2)),)
    assert factor_at(timeline, date(2023, 12, 31)) == 2
    assert factor_at(timeline, date(2024, 1, 1)) == 1


def test_the_timeline_compounds_to_the_same_number_as_the_yearly_factor() -> None:
    """The two readings are one chain, and the price side must not drift off it.

    Bradesco: bonuses approved in March 2016 and March 2017, both inside the one
    filing step from the 2015 FRE to the 2016 one. A price quoted before either
    is behind both — which is what the yearly factor for 2015 also says.
    """
    filed = {2015: Decimal(1_000_000), 2016: Decimal(1_210_000)}
    declared = [
        _action("Bonificação", 1_000_000, 1_100_000, "2016-03-10"),
        _action("Bonificação", 1_100_000, 1_210_000, "2017-03-10"),
    ]

    timeline = restatement_timeline(filed, actions=declared)
    factors = restatement_factors(filed, actions=declared)

    assert factor_at(timeline, date(2015, 6, 30)) == factors[2015] == Decimal("1.21")
    # And what the yearly reading cannot say: 2016 is not one base end to end.
    assert factor_at(timeline, date(2016, 6, 30)) == Decimal("1.1")
    assert factors[2016] == 1


def test_a_half_dated_step_falls_back_to_the_year_boundary_whole() -> None:
    # An unparseable approval date leaves one leg of a chain undatable. Splitting
    # a year on the other leg alone would apply a fraction of the step at a date
    # and the rest at none, compounding to neither reading of it.
    filed = {2024: Decimal(1_000_000), 2025: Decimal(2_000_000)}
    declared = [
        _action("Desdobramento", 1_000_000, 80_000_000, "2025-04-14"),
        _action("Grupamento", 80_000_000, 2_000_000, "0000-00-00"),
    ]

    timeline = restatement_timeline(filed, actions=declared)

    assert timeline == (RestatementStep(date(2025, 1, 1), Decimal(2)),)


def test_a_year_free_of_actions_weighs_to_the_factor_standing_over_it() -> None:
    # The refinement is a refinement: with every session on one base, the
    # session-weighted divisor is that base's factor exactly.
    timeline = (RestatementStep(date(2024, 4, 25), Decimal(2)),)
    sessions = [_close("2023-02-01", "44"), _close("2023-11-01", "46.9866")]

    assert average_factor(timeline, sessions) == 2


def test_a_year_holding_an_action_weighs_each_side_of_it() -> None:
    """Why the year's average cannot take one factor.

    A 2:1 split on 25 April: January printed 40 on the old base and October 20 on
    the new, and those are the same price. The as-traded average is 30, a number
    the share never traded at on either base; the restated average is 20, which
    is what a back-adjusted vendor series publishes for that year.
    """
    timeline = (RestatementStep(date(2024, 4, 25), Decimal(2)),)
    sessions = [_close("2024-01-15", "40"), _close("2024-10-15", "20")]

    factor = average_factor(timeline, sessions)

    assert factor == Decimal("1.5")
    assert Decimal(30) / factor == 20


def test_nothing_to_weigh_leaves_the_price_alone() -> None:
    assert average_factor((RestatementStep(date(2024, 1, 1), Decimal(2)),), []) == 1
    assert average_factor((), [_close("2024-01-15", "40")]) == 1


# --- B3's half: the exchange dates what the counts could only place in a year ---


def _exchange(
    effective: str, ratio: str, approved: str = "2024-02-02"
) -> ExchangeAction:
    return ExchangeAction(
        effective=date.fromisoformat(effective),
        approval_date=approved,
        ratio=Decimal(ratio),
    )


def test_the_exchange_dates_a_step_the_counts_could_only_place_in_a_year() -> None:
    """BBAS3, the case that made this necessary (ADR 0034).

    The FRE reports the doubled count in its **2023** filing and declares
    nothing, so the chain parked the split on 2023-01-01. It happened on
    2024-04-16, which left every 2023 session unrestated — a 100% error against
    the vendor series, and the largest single cell in the book.
    """
    filed = {2022: Decimal(2_865_417_020), 2023: Decimal(5_730_834_040)}
    exchange = [_exchange("2024-04-16", "2")]

    timeline = restatement_timeline(filed, exchange=exchange)

    assert timeline == (RestatementStep(date(2024, 4, 16), Decimal(2)),)
    assert factor_at(timeline, date(2023, 6, 30)) == 2  # was 1
    assert factor_at(timeline, date(2024, 4, 15)) == 2
    assert factor_at(timeline, date(2024, 4, 16)) == 1


def test_the_exchange_refines_a_declared_approval_into_its_ex_date() -> None:
    # CVM files the approval, B3 the last session on the old base. WEGE3's 2021
    # split was approved and traded a day apart; MGLU3's 2020 split, a week.
    filed = {2020: Decimal(2_098_658_999), 2021: Decimal(4_197_317_998)}
    declared = [_action("Desdobramento", 2_098_658_999, 4_197_317_998, "2021-04-27")]

    timeline = restatement_timeline(
        filed, actions=declared, exchange=[_exchange("2021-04-28", "2", "2021-04-27")]
    )

    assert timeline == (RestatementStep(date(2021, 4, 28), Decimal(2)),)


def test_the_exchange_never_contributes_a_ratio_of_its_own() -> None:
    """The rule that keeps this safe: it moves dates, it does not add events.

    An exchange action has no share count to anchor on, so a factor applied
    where the counts saw nothing move is exactly how one split became nine
    (#174). TOTS3's x3 is listed by B3 and is *not* in the chain, because no
    filed step accounts for it.
    """
    flat = {2019: Decimal(165_637_727), 2020: Decimal(165_637_727)}

    assert restatement_timeline(flat, exchange=[_exchange("2020-05-01", "3")]) == ()


def test_a_composite_action_is_matched_by_its_legs_compounded() -> None:
    # VIVT3 2025: B3 files a x80 split and a x0.025 grupamento on one approval
    # date. Leg by leg neither is the x2 the counts moved by; together they are.
    filed = {2023: Decimal(1_652_588_360), 2024: Decimal(3_305_176_720)}
    exchange = [
        _exchange("2025-04-15", "80", "2025-03-13"),
        _exchange("2025-04-15", "0.025", "2025-03-13"),
    ]

    timeline = restatement_timeline(filed, exchange=exchange)

    assert timeline == (RestatementStep(date(2025, 4, 15), Decimal(2)),)


def test_two_actions_of_the_same_size_in_the_window_date_nothing() -> None:
    """Ambiguity changes nothing, because choosing would be guessing.

    A company that pays a 10% bonus every year has two candidates for any step
    of 1.1, and the date is the very fact in question.
    """
    filed = {2020: Decimal(1_000_000), 2021: Decimal(1_100_000)}
    exchange = [
        _exchange("2021-03-15", "1.1", "2021-03-01"),
        _exchange("2021-09-15", "1.1", "2021-09-01"),
    ]

    timeline = restatement_timeline(filed, exchange=exchange)

    assert timeline == (RestatementStep(date(2021, 1, 1), Decimal("1.1")),)


def test_an_action_far_from_the_step_is_a_different_action() -> None:
    # The filing year can lag the event by a year, not by five. Beyond the
    # window a matching ratio is another action of the same size.
    filed = {2020: Decimal(1_000_000), 2021: Decimal(2_000_000)}

    timeline = restatement_timeline(filed, exchange=[_exchange("2015-06-01", "2")])

    assert timeline == (RestatementStep(date(2021, 1, 1), Decimal(2)),)


def test_an_action_that_could_be_either_step_dates_neither() -> None:
    """The match has to be unambiguous in both directions, not just one.

    Two x2 steps a year apart and one listed x2: it sits inside the window of
    both, and picking the nearer would be inventing the very fact — which filing
    year the action belongs to — that the counts could not establish.
    """
    filed = {
        2020: Decimal(1_000_000),
        2021: Decimal(2_000_000),
        2022: Decimal(4_000_000),
    }

    timeline = restatement_timeline(filed, exchange=[_exchange("2021-05-10", "2")])

    assert timeline == (
        RestatementStep(date(2021, 1, 1), Decimal(2)),
        RestatementStep(date(2022, 1, 1), Decimal(2)),
    )
