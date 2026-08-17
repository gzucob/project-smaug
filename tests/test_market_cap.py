"""The market cap summed over a company's listed share classes (ADR 0014)."""

from decimal import Decimal

from smaug.analysis.domain.financials import ShareCounts
from smaug.analysis.domain.indicators import NullReason
from smaug.analysis.domain.market_cap import capitalize
from smaug.portfolio.domain.share_classes import ShareClass, ShareKind
from tests.fakes import fake_classes_resolver


def test_a_single_class_company_is_its_only_class() -> None:
    cap, reason = capitalize(
        fake_classes_resolver("WEGE3"),
        ShareCounts(common=Decimal(1000), total=Decimal(1000)),
        {"WEGE3": Decimal(50)},
    )

    assert cap == Decimal(50_000)
    assert reason is None


def test_a_dual_class_company_pays_each_class_its_own_price() -> None:
    # The whole point: PETR3 and PETR4 do not trade at the same price, so pricing
    # every share at the analyzed ticker's quote (10 × 1200 = 12000) misprices the
    # company by the spread between the classes.
    cap, reason = capitalize(
        fake_classes_resolver("PETR4"),
        ShareCounts(common=Decimal(800), preferred=Decimal(400), total=Decimal(1200)),
        {"PETR3": Decimal(12), "PETR4": Decimal(10)},
    )

    assert cap == Decimal(13_600)  # 12 × 800 + 10 × 400
    assert reason is None


def test_pna_and_pnb_each_use_their_own_filed_count_and_price() -> None:
    # Banrisul's exact FRE 2025 counts provide a primary-source reconciliation for
    # the three-term invariant; prices are deliberately distinct so any reuse of
    # the aggregate preferred count fails loudly.
    classes = (
        ShareClass("BRSR3", ShareKind.COMMON),
        ShareClass("BRSR5", ShareKind.PREFERRED),
        ShareClass("BRSR6", ShareKind.PREFERRED),
    )
    counts = ShareCounts(
        common=Decimal(205_064_841),
        preferred=Decimal(203_909_636),
        total=Decimal(408_974_477),
        preferred_a=Decimal(1_373_091),
        preferred_b=Decimal(202_536_545),
    )

    cap, reason = capitalize(
        classes,
        counts,
        {"BRSR3": Decimal(10), "BRSR5": Decimal(12), "BRSR6": Decimal(14)},
    )

    assert cap == (
        Decimal(205_064_841) * 10 + Decimal(1_373_091) * 12 + Decimal(202_536_545) * 14
    )
    assert reason is None


def test_pna_without_a_class_specific_count_nulls_the_whole_cap() -> None:
    cap, reason = capitalize(
        (ShareClass("BRSR5", ShareKind.PREFERRED),),
        ShareCounts(preferred=Decimal(203_909_636), total=Decimal(408_974_477)),
        {"BRSR5": Decimal(12)},
    )

    assert cap is None
    assert reason is NullReason.MISSING_SHARE_COUNT


def test_a_unit_is_capitalized_without_its_bundle_composition() -> None:
    # SAPR11's own quote never enters the sum — the underlying classes do, which
    # is why the cap needs no answer to "how many shares are in a unit" (#38).
    cap, reason = capitalize(
        fake_classes_resolver("SAPR11"),
        ShareCounts(common=Decimal(500), preferred=Decimal(1000), total=Decimal(1500)),
        {"SAPR3": Decimal(8), "SAPR4": Decimal(7), "SAPR11": Decimal(22)},
    )

    assert cap == Decimal(11_000)  # 8 × 500 + 7 × 1000
    assert reason is None


def test_a_class_without_a_price_nulls_the_whole_cap() -> None:
    # Half a company is a wrong number, not a partial one.
    cap, reason = capitalize(
        fake_classes_resolver("PETR4"),
        ShareCounts(common=Decimal(800), preferred=Decimal(400), total=Decimal(1200)),
        {"PETR3": None, "PETR4": Decimal(10)},
    )

    assert cap is None
    assert reason is NullReason.MISSING_PRICE


def test_a_class_price_reason_survives_into_the_cap() -> None:
    cap, reason = capitalize(
        fake_classes_resolver("PETR4"),
        ShareCounts(common=Decimal(800), preferred=Decimal(400), total=Decimal(1200)),
        {"PETR3": None, "PETR4": Decimal(10)},
        price_null_reasons={"PETR3": NullReason.PRICE_SYMBOL_NOT_FOUND},
    )

    assert cap is None
    assert reason is NullReason.PRICE_SYMBOL_NOT_FOUND


def test_a_class_without_a_filed_count_nulls_the_whole_cap() -> None:
    cap, reason = capitalize(
        fake_classes_resolver("PETR4"),
        ShareCounts(common=Decimal(800), preferred=None, total=Decimal(1200)),
        {"PETR3": Decimal(12), "PETR4": Decimal(10)},
    )

    assert cap is None
    assert reason is NullReason.MISSING_SHARE_COUNT


def test_no_filing_at_all_names_the_missing_share_count() -> None:
    prices = {"PETR3": Decimal(12), "PETR4": Decimal(10)}
    cap, reason = capitalize(fake_classes_resolver("PETR4"), None, prices)

    assert cap is None
    assert reason is NullReason.MISSING_SHARE_COUNT


def test_no_known_classes_names_the_missing_share_count() -> None:
    # An on-demand ticker whose classes could not be resolved: cap stays a named
    # null rather than a guess (the resolver returns an empty tuple).
    cap, reason = capitalize(
        (), ShareCounts(common=Decimal(100), total=Decimal(100)), {}
    )

    assert cap is None
    assert reason is NullReason.MISSING_SHARE_COUNT
