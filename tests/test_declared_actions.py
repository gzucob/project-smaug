"""restatement_factors with the corporate actions CVM declares.

The declared ratio outranks the one inferred from consecutive filed counts,
because the filed counts also move on issuances and cancellations. Every fixture
here is a real filing.
"""

from datetime import date
from decimal import Decimal

from smaug.analysis.domain.capital import (
    BaseChange,
    CorporateAction,
    restatement_factors,
)


def _action(kind: str, before: int, after: int, date: str = "2015-12-15"):
    return CorporateAction(
        approval_date=date,
        kind=kind,
        total_before=Decimal(before),
        total_after=Decimal(after),
    )


def test_the_declared_ratio_beats_the_one_inferred_from_the_counts() -> None:
    # Ampla. The FRE counts move 3,922,515,918,446 -> 166,634,326 across two
    # filings, a ratio of 1/23,539 that the inference took for a corporate
    # action. CVM declares the grupamento as 1/40,000; the remainder is the
    # share issue that raised capital from R$1.298 bn to R$2.498 bn.
    filed = {2016: Decimal(3_922_515_918_446), 2017: Decimal(166_634_326)}
    declared = [_action("Grupamento", 3_922_515_918_446, 98_062_897)]

    inferred = restatement_factors(filed)
    factors = restatement_factors(filed, actions=declared)

    # The inference lands near 1/23,539 — the grupamento and the issue compounded.
    assert Decimal("0.0000424") < inferred[2016] < Decimal("0.0000425")
    # The declared one is 1/40,000 within the fractional shares the grupamento
    # rounded away — exactly the kind of near-miss a clean-ratio test has to
    # tolerate and a declaration does not have to guess at.
    assert abs(factors[2016] - Decimal(1) / Decimal(40000)) < Decimal("1e-11")
    assert factors[2017] == 1  # the latest filed year is always the base


def test_a_composite_action_compounds_through_its_chain() -> None:
    # VIVT3: a split and a grupamento on one date, which ADR 0027 states it
    # cannot detect because their combined FRE ratio is dirty. Declared, each
    # leg names its own base, so the chain resolves to the x2 it really was.
    filed = {2024: Decimal(1_000_000), 2025: Decimal(2_000_000)}
    declared = [
        _action("Desdobramento", 1_000_000, 80_000_000, "2025-04-14"),
        _action("Grupamento", 80_000_000, 2_000_000, "2025-04-14"),
    ]

    factors = restatement_factors(filed, actions=declared)

    assert factors[2024] == 2


def test_an_action_that_does_not_start_from_a_filed_count_is_ignored() -> None:
    # It describes a different share base — a later event, or one the FRE year
    # in hand predates. Falling through to the inference is what keeps an
    # unrelated declaration from rewriting a year it never touched.
    filed = {2020: Decimal(100), 2021: Decimal(200)}
    declared = [_action("Desdobramento", 999_999, 1_999_998)]

    factors = restatement_factors(filed, actions=declared)

    assert factors[2020] == 2  # inferred, as before


def test_an_event_filed_with_a_zero_count_falls_back_to_the_inference() -> None:
    # 21 of the 538 declared events carry a zero on one side. The mirror keeps
    # them (ADR 0016); a ratio cannot be taken from one.
    filed = {2020: Decimal(100), 2021: Decimal(300)}
    declared = [_action("Desdobramento", 100, 0)]

    factors = restatement_factors(filed, actions=declared)

    assert factors[2020] == 3


def test_a_declared_bonus_series_compounds_year_after_year() -> None:
    # Bradesco's 10% bonus, declared with a date every year. No other source has
    # the series: B3's feed lists one of them.
    filed = {
        2016: Decimal(5_048_728_847),
        2017: Decimal(5_553_601_732),
        2018: Decimal(6_108_961_905),
        2019: Decimal(6_719_858_095),
    }
    declared = [
        _action("Bonificação", 5_048_728_847, 5_553_601_732, "2016-03-10"),
        _action("Bonificação", 5_553_601_732, 6_108_961_905, "2017-03-10"),
        _action("Bonificação", 6_108_961_905, 6_719_858_095, "2018-03-12"),
    ]

    factors = restatement_factors(filed, actions=declared)

    assert factors[2019] == 1
    assert factors[2018] == Decimal(6_719_858_095) / Decimal(6_108_961_905)
    # Three 10% bonuses compound to ~1.331 over the window.
    assert Decimal("1.33") < factors[2016] < Decimal("1.34")


def test_one_filing_step_can_hold_more_than_one_action() -> None:
    # The FRE year lags what it reports. Bradesco's count moves x1.21 between its
    # 2015 and 2016 filings, and the two 10% bonuses that make it up were
    # approved in March 2016 and March 2017. Taking only the first reads 1.10
    # where 1.21 happened, and the shortfall compounds down the whole series.
    filed = {2015: Decimal(5_048_728_847), 2016: Decimal(6_108_961_905)}
    declared = [
        _action("Bonificação", 5_048_728_847, 5_553_601_732, "2016-03-10"),
        _action("Bonificação", 5_553_601_732, 6_108_961_905, "2017-03-10"),
    ]

    factors = restatement_factors(filed, actions=declared)

    # The two bonuses compound to the whole step, 1.21 — not the 1.10 the first
    # of them accounts for. (Compared with a tolerance because a product of two
    # divisions and one division differ in the last decimal place.)
    assert abs(factors[2015] - Decimal("1.21")) < Decimal("1e-9")


def test_the_chain_stops_at_the_next_filing_and_leaves_the_issuance_out() -> None:
    # Ampla again: the chain ends at the grupamento's 98,062,897 while the next
    # filing says 166,634,326. The shares in between were sold, not split, and
    # restating them would rewrite a dilution as a corporate action.
    filed = {2016: Decimal(3_922_515_918_446), 2017: Decimal(166_634_326)}
    declared = [_action("Grupamento", 3_922_515_918_446, 98_062_897)]

    factors = restatement_factors(filed, actions=declared)

    assert abs(factors[2016] - Decimal(1) / Decimal(40000)) < Decimal("1e-11")


def test_a_standstill_year_does_not_reapply_the_same_action() -> None:
    # EALT3 files 22.5 M unchanged from 2015 to 2023 and declares one x10 split.
    # Matching on the count alone re-applied it at every standstill year: nine
    # times over, for 2.25e16 shares and a market cap of R$40 quadrillion.
    filed = {year: Decimal(22_500_000) for year in range(2015, 2024)}
    filed[2024] = Decimal(225_000_000)
    declared = [_action("Desdobramento", 22_500_000, 225_000_000, "2024-04-30")]

    factors = restatement_factors(filed, actions=declared)

    assert factors[2024] == 1
    assert all(factors[year] == 10 for year in range(2015, 2024))


def test_an_action_is_spent_once_when_two_steps_could_claim_it() -> None:
    # The count returns to the level the action started from, so two different
    # steps begin at 100 and both match the one declared event. It is spent on
    # the first that claims it (newest first) and the other falls through to the
    # inference — the guarantee being that a single declaration never restates
    # two separate steps.
    filed = {
        2018: Decimal(100),
        2019: Decimal(200),
        2020: Decimal(100),
        2021: Decimal(200),
    }
    declared = [_action("Desdobramento", 100, 200, "2021-05-05")]

    with_declared = restatement_factors(filed, actions=declared)
    inferred_only = restatement_factors(filed)

    # Every step here is a clean ratio, so the inference reaches the same place:
    # what matters is that adding the declaration changed nothing, rather than
    # doubling a step it had already accounted for.
    assert with_declared == inferred_only
    assert with_declared[2018] == 2


def test_an_issuance_still_contributes_nothing() -> None:
    # The declared file is silent on issuances, so this falls to the inference,
    # which reads the dirty ratio as the dilution it is (ADR 0027).
    filed = {2021: Decimal(1_000_000), 2022: Decimal(1_835_400)}

    factors = restatement_factors(filed, actions=[])

    assert factors[2021] == 1


def test_an_action_anchors_on_the_count_it_ended_at_when_its_start_was_unfiled() -> (
    None
):
    """TOTS3. Its 3:1 split declares 192,637,727 -> 577,913,181 and no FRE filed
    the first of those: 27 million shares were issued between the 2018 filing and
    the split, so the ``before`` matches nothing while the ``after`` matches the
    2019 filing to the share.

    Read only forwards the action anchors on nothing and the chain falls through
    to the inference, which is dirty for the same reason — leaving TOTS3 200% out
    against the vendor series for 2015-2019 (#176).
    """
    filed = {2018: Decimal(165_637_727), 2019: Decimal(577_913_181)}
    declared = [
        _action("Desdobramento", 192_637_727, 577_913_181, "2020-04-27"),
    ]

    inferred = restatement_factors(filed)
    factors = restatement_factors(filed, actions=declared)

    # 577,913,181 / 165,637,727 is 3.489x, dirty enough that the inference
    # declines it and leaves the years unrestated.
    assert inferred[2018] == 1
    assert factors[2018] == 3
    assert factors[2019] == 1


def test_the_forward_anchor_is_preferred_where_both_ends_could_match() -> None:
    # Reading backwards is the fallback, not a second chance: an action that
    # starts where the filing starts is the one that describes the move, and the
    # count it ends at is not consulted.
    filed = {2020: Decimal(100), 2021: Decimal(300)}
    declared = [
        _action("Desdobramento", 100, 300, "2021-03-01"),
        _action("Bonificação", 150, 300, "2021-06-01"),
    ]

    factors = restatement_factors(filed, actions=declared)

    assert factors[2020] == 3


def test_an_action_ending_at_an_unfiled_count_is_still_ignored() -> None:
    # Neither end matches, so it describes a different share base — the guard
    # that keeps an unrelated declaration from rewriting a year it never touched
    # has to survive the second anchor.
    filed = {2020: Decimal(100), 2021: Decimal(200)}
    declared = [_action("Desdobramento", 999_999, 1_999_998)]

    factors = restatement_factors(filed, actions=declared)

    assert factors[2020] == 2  # inferred, as before


def _change(day: str, ratio: str) -> BaseChange:
    return BaseChange(session=date.fromisoformat(day), ratio=Decimal(ratio))


def test_a_dirty_ratio_two_witnesses_agree_on_is_restated() -> None:
    """Méliuz's 1:10 grupamento, with half a percent of the base issued alongside it.

    The counts read 0.10051 where the event was 1/10, which the clean-ratio test
    rejects by three orders of magnitude — and the price for every year before it
    stayed ten times too small. B3's tape marks the base change on 2023-06-01 and
    the market priced it at 0.0989, so both readings say the same thing.
    """
    filed = {2022: Decimal(865_180_443), 2023: Decimal(86_957_953)}
    tape = [_change("2023-06-01", "0.0989")]

    assert restatement_factors(filed)[2022] == 1  # nothing, before the witness
    assert restatement_factors(filed, changes=tape)[2022] == Decimal("0.1")


def test_a_move_too_small_to_tell_from_an_issuance_is_left_alone() -> None:
    """A 4% bonus and a 4% follow-on are the same number.

    The tape reads a size only to ±25%, so it cannot corroborate an action
    smaller than its own error. Measured against the vendor series, every cell
    this rule got wrong sat here: Cyrela went from an exact match to 16% out in
    all eleven years, and Localiza from 0.4% to 3.5%.
    """
    filed = {2024: Decimal(1_000_000), 2025: Decimal(1_038_460)}
    tape = [_change("2025-12-30", "1.0324")]

    assert restatement_factors(filed, changes=tape)[2024] == 1


def test_a_plausible_ratio_the_tape_did_not_see_is_left_alone() -> None:
    # Measured over the exchange, 69 gaps sit near a plausible action ratio with
    # no base change marked anywhere in them. They are issuances that happen to
    # land near a fraction, and restating them would rewrite a dilution.
    filed = {2022: Decimal(1_000_000), 2023: Decimal(2_010_000)}

    assert restatement_factors(filed, changes=[])[2022] == 1


def test_a_tape_event_of_another_size_does_not_confirm_the_ratio() -> None:
    # The window is two years wide, so it catches actions belonging to other
    # gaps. A 1:10 grupamento is not evidence for a 5% move.
    filed = {2022: Decimal(1_000_000), 2023: Decimal(2_010_000)}
    tape = [_change("2023-06-01", "0.0989")]

    assert restatement_factors(filed, changes=tape)[2022] == 1


def test_a_falling_count_is_not_explained_by_an_event_that_handed_shares_out() -> None:
    # LREN3's 2021->2022 move is a buyback cancellation and the nearest thing on
    # the tape is a bonus. Same magnitude, opposite direction, different event.
    filed = {2021: Decimal(1_000_000), 2022: Decimal(505_100)}
    tape = [_change("2022-04-05", "1.96")]

    assert restatement_factors(filed, changes=tape)[2021] == 1


def test_two_base_changes_in_one_gap_leave_it_unexplained() -> None:
    """Recrusul's shape: the counts cannot say which of them they moved by.

    A single filed ratio holding two actions and an issuance is not separable by
    rounding, and picking one would be choosing the answer.
    """
    filed = {2022: Decimal(1_000_000), 2023: Decimal(505_100)}
    tape = [_change("2023-03-01", "0.5"), _change("2023-09-01", "0.98")]

    assert restatement_factors(filed, changes=tape)[2022] == 1


def test_the_declared_ratio_still_outranks_the_witnessed_one() -> None:
    # The tape is the last fallback, not a competitor: where CVM declares the
    # move, its exact ratio is the answer and the market's reading is not.
    filed = {2022: Decimal(1_000_000), 2023: Decimal(2_010_000)}
    declared = [_action("Desdobramento", 1_000_000, 2_000_000, "2023-03-01")]
    tape = [_change("2023-03-02", "2.05")]

    factors = restatement_factors(filed, actions=declared, changes=tape)

    assert factors[2022] == 2  # the declared 2x, not a witnessed 2.01
