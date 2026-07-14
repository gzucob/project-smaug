"""Outstanding shares: the issued count net of treasury, at the filer's own scale."""

from decimal import Decimal

from smaug.analysis.domain.capital import filed_scale, outstanding_counts
from smaug.analysis.domain.financials import CapitalComposition, ShareCounts


def _issued(common: int, preferred: int, total: int) -> ShareCounts:
    return ShareCounts(
        common=Decimal(common), preferred=Decimal(preferred), total=Decimal(total)
    )


def _filed(
    issued_total: int,
    *,
    common: int = 0,
    preferred: int = 0,
    total: int | None = None,
) -> CapitalComposition:
    return CapitalComposition(
        issued_total=Decimal(issued_total),
        treasury_common=Decimal(common),
        treasury_preferred=Decimal(preferred),
        treasury_total=Decimal(total if total is not None else common + preferred),
    )


def test_a_composition_filed_in_units_reconciles_at_scale_one() -> None:
    # PETR4 files units: its composition's total is the FRE's total.
    assert filed_scale(Decimal(12_888_732_761), Decimal(13_044_496_930)) == Decimal(1)


def test_a_composition_filed_in_thousands_reconciles_at_scale_one_thousand() -> None:
    # VALE3 files thousands, with no column saying so — 4,539,008 is 4.54 bn shares.
    assert filed_scale(Decimal(4_439_159_764), Decimal(4_539_008)) == Decimal(1000)


def test_a_scale_that_reconciles_to_neither_is_none() -> None:
    # Two filings a hundredfold apart are not the same company's shares. Guessing a
    # scale here would be a 1000x error to correct a 3% one.
    assert filed_scale(Decimal(4_439_159_764), Decimal(45_000_000_000)) is None
    assert filed_scale(Decimal(4_439_159_764), Decimal(0)) is None
    assert filed_scale(Decimal(4_439_159_764), None) is None


def test_treasury_is_subtracted_from_a_units_filer_class_by_class() -> None:
    # PETR4 2024: the buyback is all in the PN class (155.5 M of 5.45 bn).
    counts = outstanding_counts(
        _issued(7_442_231_382, 5_446_501_379, 12_888_732_761),
        _filed(13_044_496_930, common=222_760, preferred=155_541_409),
    )

    assert counts == ShareCounts(
        common=Decimal(7_442_008_622),
        preferred=Decimal(5_290_959_970),
        total=Decimal(12_732_968_592),
    )


def test_treasury_of_a_thousands_filer_is_scaled_before_subtracting() -> None:
    # VALE3 2025: 270,228 filed in thousands is 270.2 M shares — 6% of the company,
    # not 0.006%. Subtracting it unscaled would be indistinguishable from doing
    # nothing at all.
    counts = outstanding_counts(
        _issued(4_439_159_752, 12, 4_439_159_764),
        _filed(4_539_008, common=270_228),
    )

    assert counts is not None
    assert counts.common == Decimal(4_168_931_752)
    assert counts.total == Decimal(4_168_931_764)


def test_a_negative_treasury_count_voids_the_reading() -> None:
    # BBDC4's 2022 composition files a negative treasury count. Subtracting it would
    # *inflate* the company; a company cannot hold fewer than none of its own shares.
    assert (
        outstanding_counts(
            _issued(5_338_393_881, 5_320_094_147, 10_658_488_028),
            _filed(10_658_488, common=-8_089, preferred=-8_229),
        )
        is None
    )


def test_no_composition_on_file_yields_none() -> None:
    assert outstanding_counts(_issued(100, 50, 150), None) is None


def test_a_treasury_stake_that_swallows_a_class_voids_the_reading() -> None:
    # Not a reading of the filing but a mismatch between two of them — the
    # composition may predate a split the FRE already reflects.
    assert (
        outstanding_counts(
            _issued(1_000_000, 0, 1_000_000),
            _filed(1_000_000, common=1_000_000),
        )
        is None
    )
