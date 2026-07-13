"""The market cap summed over a company's listed share classes (ADR 0014)."""

from decimal import Decimal

from smaug.analysis.domain.financials import ShareCounts
from smaug.analysis.domain.indicators import NullReason
from smaug.analysis.domain.market_cap import capitalize


def test_a_single_class_company_is_its_only_class() -> None:
    cap, reason = capitalize(
        "WEGE3",
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
        "PETR4",
        ShareCounts(common=Decimal(800), preferred=Decimal(400), total=Decimal(1200)),
        {"PETR3": Decimal(12), "PETR4": Decimal(10)},
    )

    assert cap == Decimal(13_600)  # 12 × 800 + 10 × 400
    assert reason is None


def test_a_unit_is_capitalized_without_its_bundle_composition() -> None:
    # SAPR11's own quote never enters the sum — the underlying classes do, which
    # is why the cap needs no answer to "how many shares are in a unit" (#38).
    cap, reason = capitalize(
        "SAPR11",
        ShareCounts(common=Decimal(500), preferred=Decimal(1000), total=Decimal(1500)),
        {"SAPR3": Decimal(8), "SAPR4": Decimal(7), "SAPR11": Decimal(22)},
    )

    assert cap == Decimal(11_000)  # 8 × 500 + 7 × 1000
    assert reason is None


def test_a_class_without_a_price_nulls_the_whole_cap() -> None:
    # Half a company is a wrong number, not a partial one.
    cap, reason = capitalize(
        "PETR4",
        ShareCounts(common=Decimal(800), preferred=Decimal(400), total=Decimal(1200)),
        {"PETR3": None, "PETR4": Decimal(10)},
    )

    assert cap is None
    assert reason is NullReason.MISSING_PRICE


def test_a_class_without_a_filed_count_nulls_the_whole_cap() -> None:
    cap, reason = capitalize(
        "PETR4",
        ShareCounts(common=Decimal(800), preferred=None, total=Decimal(1200)),
        {"PETR3": Decimal(12), "PETR4": Decimal(10)},
    )

    assert cap is None
    assert reason is NullReason.MISSING_SHARE_COUNT


def test_no_filing_at_all_names_the_missing_share_count() -> None:
    prices = {"PETR3": Decimal(12), "PETR4": Decimal(10)}
    cap, reason = capitalize("PETR4", None, prices)

    assert cap is None
    assert reason is NullReason.MISSING_SHARE_COUNT
