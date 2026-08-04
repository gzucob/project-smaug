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
    ExchangeAction,
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


def test_the_feed_corrects_a_grid_guess_that_matched_the_wrong_event() -> None:
    """BRML3 (#202): a gap still waiting on its next FRE reaches, through the
    window's filing-lag margin, into a real and differently-sized action that
    belongs to that future gap. The grid finds a plausible ratio near the
    dirty filed number (24/17); B3's feed and the tape agree with *each
    other* on a much smaller one instead.
    """
    filed = {2016: Decimal(1_000_000), 2017: Decimal(1_406_920)}  # dirty, 40.69%
    tape = [_change("2017-05-02", "1.15448")]
    exchange = [_exchange("2017-04-29", "1.15", "2017-04-28")]

    grid_only = restatement_factors(filed, changes=tape)
    with_feed = restatement_factors(filed, changes=tape, exchange=exchange)

    # Without the feed, the grid's nearest plausible fraction (24/17) clears
    # the tape's ±25% tolerance and is used.
    assert abs(grid_only[2016] - Decimal(24) / Decimal(17)) < Decimal("0.0001")
    # With it, the feed's own factor — closer to what the tape actually saw —
    # replaces the guess.
    assert with_feed[2016] == Decimal("1.15")


def test_the_feed_does_not_override_a_guess_it_explains_worse() -> None:
    """The feed only outranks the grid's guess when it is the *better*
    explanation of what the tape saw (#202) — a feed present in the window is
    not enough on its own, since the tape's own session can carry noise of
    its own (up to 10% at its worst, ADR 0035).
    """
    filed = {2023: Decimal(1_000_000), 2024: Decimal(1_612_000)}  # dirty, 61.2%
    tape = [_change("2024-06-01", "1.65")]
    exchange = [_exchange("2024-05-30", "1.5", "2024-05-20")]

    factors = restatement_factors(filed, changes=tape, exchange=exchange)

    # The grid's 21/13 (1.61538) is closer to the tape's 1.65 than the feed's
    # 1.5 is, so it is kept.
    assert abs(factors[2023] - Decimal(21) / Decimal(13)) < Decimal("0.0001")


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


def _exchange(day: str, ratio: str, approved: str = "2026-01-01") -> ExchangeAction:
    return ExchangeAction(
        effective=date.fromisoformat(day),
        approval_date=approved,
        ratio=Decimal(ratio),
    )


def test_the_feed_explains_a_gap_holding_several_actions_when_the_counts_agree() -> (
    None
):
    """Sabesp: a 2.96% bonus, a 0.16% bonus and a 1:5 split between two filings.

    No single ratio explains x5.15652 and no rounding reaches it, but the three
    factors B3 publishes compound to exactly that — which is the counts and the
    exchange, two records that cannot see each other, stating the same move.
    """
    filed = {2024: Decimal(683_509_869), 2025: Decimal(3_524_534_028)}
    exchange = [
        _exchange("2025-12-24", "1.0296469750"),
        _exchange("2026-03-20", "1.001609803220"),
        _exchange("2026-04-29", "5"),
    ]

    assert restatement_factors(filed)[2024] == 1  # nothing, before the feed
    applied = restatement_factors(filed, exchange=exchange)[2024]
    # The counts moved 3,524,534,028 / 683,509,869 = 5.15652, and the factor is
    # the exchange's exact product rather than that quotient.
    assert abs(applied - Decimal("5.15652")) < Decimal("0.0001")


def test_the_feed_is_ignored_where_the_counts_do_not_confirm_it() -> None:
    # The refusal ADR 0034 wrote down: a factor applied where the counts saw
    # nothing of the sort is how one split compounded into nine. Oi's counts move
    # x5 across a filing that also holds a 1:10 grupamento — a debt conversion
    # rode along, and no product of factors is that move.
    filed = {2023: Decimal(66_030_374), 2024: Decimal(330_121_738)}
    exchange = [_exchange("2024-06-15", "0.1")]

    assert restatement_factors(filed, exchange=exchange)[2023] == 1


def test_an_action_the_earlier_count_already_reflects_is_not_counted_twice() -> None:
    """LREN3's September 2015 split is inside the 2015 filing already.

    A window reaching back into the earlier filing year offers it to the
    2015->2016 gap, whose counts moved 1.111 — nothing like x5.
    """
    filed = {2015: Decimal(640_041_325), 2016: Decimal(711_145_682)}
    exchange = [_exchange("2015-09-24", "5")]

    assert restatement_factors(filed, exchange=exchange)[2015] == 1


def test_a_gap_spanning_several_filings_sees_every_action_inside_it() -> None:
    """Recrusul files nothing for 2023 or 2024, so one gap runs four years.

    Four base changes fall inside it, and a window that saw only the last would
    match that one to the move the other three made. Ambiguity is the honest
    answer here, not a factor.
    """
    filed = {2022: Decimal(37_911_977), 2025: Decimal(110_250_240)}
    tape = [
        _change("2023-07-10", "0.5219"),
        _change("2024-05-31", "0.2621"),
        _change("2026-03-03", "3.6429"),
    ]

    assert restatement_factors(filed, changes=tape)[2022] == 1


def test_a_declaration_stranded_between_two_issuances_still_counts() -> None:
    # Veste (ex-Le Lis Blanc). Its 8:1 grupamento declares 848,591,865 ->
    # 106,073,983, and neither end matches a filing: shares were issued on both
    # sides of it, so the FRE files 68.9 M for 2021 and 113.4 M for 2022. The
    # filed move is a dirty x1.6475 that no rule explains, and the ratio CVM
    # states outright used to be dropped with it (#197).
    filed = {2021: Decimal(68_850_829), 2022: Decimal(113_426_924)}
    declared = [_action("Grupamento", 848_591_865, 106_073_983, "2022-12-14")]
    # The session VSTE3 replaced LLIS3: the only witness, because B3's tape marks
    # nothing on a code change and the counts never carried the move.
    seam = [BaseChange(date(2023, 2, 9), Decimal("1.73") / Decimal("12.93"), seam=True)]

    inferred = restatement_factors(filed)
    factors = restatement_factors(filed, actions=declared, changes=seam)

    assert inferred[2021] == 1  # a dirty ratio restates nothing, by design
    assert factors[2021] == Decimal(106_073_983) / Decimal(848_591_865)
    assert factors[2022] == 1


def test_a_stranded_declaration_needs_the_seam_to_speak() -> None:
    # The same declaration with no seam under it stays dropped: an approval date
    # alone re-applies actions the counts already carried (measured: 60 of 368
    # registrants moved, Alpargatas' 1.25 landing on top of itself).
    filed = {2021: Decimal(68_850_829), 2022: Decimal(113_426_924)}
    declared = [_action("Grupamento", 848_591_865, 106_073_983, "2022-12-14")]
    marked = [BaseChange(date(2023, 2, 9), Decimal("1.73") / Decimal("12.93"))]

    assert restatement_factors(filed, actions=declared)[2021] == 1
    # Nor on an ordinary base change: that one the counts can already anchor.
    assert restatement_factors(filed, actions=declared, changes=marked)[2021] == 1


def test_a_stranded_declaration_the_counts_already_carried_is_not_applied_twice() -> (
    None
):
    # Alpargatas: the counts move x1.2500000016 in one filing gap while the
    # declaration of the same 1.25 sits in the neighbouring gap's window — the
    # two windows overlap by a year by construction.
    filed = {
        2018: Decimal(100_000_000),
        2019: Decimal(125_000_016),
        2020: Decimal(125_000_016),
    }
    declared = [_action("Bonificação", 100_000_000, 125_000_000, "2019-03-21")]
    seam = [BaseChange(date(2020, 3, 21), Decimal("1") / Decimal("1.25"), seam=True)]

    factors = restatement_factors(filed, actions=declared, changes=seam)

    assert factors[2019] == 1  # the standstill year, not a second bonus
    assert abs(factors[2018] - Decimal("1.25")) < Decimal("0.001")


def test_a_declaration_outside_the_gap_is_left_where_it_belongs() -> None:
    # The window is what places a stranded declaration, so it is the one thing
    # that has to be strict.
    filed = {2021: Decimal(68_850_829), 2022: Decimal(113_426_924)}
    declared = [_action("Grupamento", 848_591_865, 106_073_983, "2024-06-01")]
    seam = [BaseChange(date(2023, 2, 9), Decimal("1.73") / Decimal("12.93"), seam=True)]

    assert restatement_factors(filed, actions=declared, changes=seam)[2021] == 1


def test_a_stranded_declaration_never_outranks_a_rule_that_answered() -> None:
    # A clean filed move is explained already; a declaration inside the same
    # window does not get to compound on top of it.
    filed = {2021: Decimal(50_000_000), 2022: Decimal(100_000_000)}
    declared = [_action("Grupamento", 848_591_865, 106_073_983, "2022-12-14")]
    seam = [BaseChange(date(2023, 2, 9), Decimal("1.73") / Decimal("12.93"), seam=True)]

    assert restatement_factors(filed, actions=declared, changes=seam)[2021] == 2
