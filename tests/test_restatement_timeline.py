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
