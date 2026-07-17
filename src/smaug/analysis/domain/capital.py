"""Outstanding shares: what the company issued, less what it holds in treasury.

A share the company bought back is issued but **not outstanding** — it draws no
dividend and carries no claim on earnings. The market cap should not price it, and
the per-share indicators should not divide by it, so both read the counts through
here (ADR 0017). The stake is small (0.4%–3% for most of the portfolio) but real,
systematically in one direction, and for VALE3 it reaches 6%.

Treasury shares are filed only in the statements' ``composicao_capital`` member,
never in the FRE the issued counts come from — and that member is filed **at the
filer's own scale, with no column saying which**: TAEE11, VALE3 and CXSE3 file
thousands, PETR4, BBAS3 and WEGE3 file units, and BBDC4 changed from one to the
other between 2024 and 2025. So the scale is a fact to be *derived*, not assumed:
the composition files its own issued total, which is the same quantity the FRE
reports, and the ratio between the two is the multiple.

Everything here is pure: a filing that cannot be read yields ``None`` and the
caller keeps the issued count, rather than a treasury figure guessed at the wrong
scale — which, at 1000x, would be a far larger error than the one it corrects.
"""

from __future__ import annotations

from collections.abc import Mapping
from decimal import ROUND_HALF_EVEN, Decimal

from smaug.analysis.domain.financials import CapitalComposition, ShareCounts

_UNITS = Decimal(1)
_THOUSANDS = Decimal(1000)

# The two scales are three orders of magnitude apart, so anything nearer to 1 than
# to 1000 *in ratio* is units. The boundary is their geometric midpoint (√1000).
_BOUNDARY = Decimal("31.62")

# How far the reconciliation may still miss after the scale is applied. The FRE and
# the statements are filed months apart and the composition may predate a split, so
# an exact match is not on offer — but a 10x gap means the two are not the same
# company's shares and the scale has not been established at all.
_TOLERANCE = Decimal(10)


def filed_scale(
    issued_total: Decimal, composition_total: Decimal | None
) -> Decimal | None:
    """The multiple ``composition_total`` is filed in: 1, 1000, or ``None`` if unclear.

    Reconciled against the FRE's ``issued_total`` for the same year — the one
    cross-check available, since the member itself says nothing about its scale.
    """
    if composition_total is None or composition_total <= 0 or issued_total <= 0:
        return None
    ratio = issued_total / composition_total
    scale = _UNITS if ratio < _BOUNDARY else _THOUSANDS
    reconciled = ratio / scale
    if not (1 / _TOLERANCE <= reconciled <= _TOLERANCE):
        return None
    return scale


def outstanding_counts(
    issued: ShareCounts, composition: CapitalComposition | None
) -> ShareCounts | None:
    """``issued`` net of treasury, or ``None`` when the filing cannot be read.

    ``None`` is not "no treasury shares" — it is "we do not know how many", and the
    caller answers it by keeping the issued count (an over-count of a few percent),
    never by subtracting a figure whose scale it had to guess.
    """
    if composition is None or issued.total is None:
        return None
    scale = filed_scale(issued.total, composition.issued_total)
    if scale is None:
        return None

    # BBDC4 files *negative* treasury counts (2022, Q1-2024). Decided from the
    # filings themselves (#88): the magnitude is the balance and the sign is
    # noise. The DMPL's treasury cost trail settles it — BBDC4 sold its entire
    # 2021 lot (R$666,702k) during 2022 and bought a new one costing R$224,377k,
    # which it sold whole during 2023 (composition 2023 = 0/0). That cost over
    # the filed |16,318k| shares is R$13.75/share, the 2022 market price; and a
    # movement reading is arithmetically impossible for Q1-2024, whose opening
    # balance was zero. So the count is read as its absolute value.
    net = ShareCounts(
        common=_net(issued.common, composition.treasury_common, scale),
        preferred=_net(issued.preferred, composition.treasury_preferred, scale),
        total=_net(issued.total, composition.treasury_total, scale),
    )
    # A company cannot hold every share it issued. A class that nets to nothing means
    # the two filings are not describing the same shares — the composition may predate
    # a split the FRE already reflects — and the whole reading is void, not repaired
    # class by class.
    if any(
        count is not None and count <= 0
        for count in (net.common, net.preferred, net.total)
    ):
        return None
    return net


def _net(
    issued: Decimal | None, treasury: Decimal | None, scale: Decimal
) -> Decimal | None:
    """One class's outstanding count. A class the filer does not have stays ``None``.

    ``abs()`` because a treasury *balance* cannot be negative — the sign on
    BBDC4's filings is noise, not meaning (see ``outstanding_counts``).
    """
    if issued is None:
        return None
    return issued if treasury is None else issued - abs(treasury) * scale


# A corporate action on the whole share base (split, grupamento, bonificação)
# multiplies the count by a *clean* small rational, exact to the share — BBAS3's
# 2023 bonus is ×2 to the digit, SANEPAR's 2020 is ×3, HAPVIDA's 2025 grupamento
# is ÷15 within the fraction the company rounded away. A real issuance is dirty:
# HAPVIDA's 2022 merger multiplied the count by 1.8354. The denominator bound and
# the relative tolerance draw that line; the ADR (0027) records the residual risk
# of an issuance landing on a clean ratio to the share, which nothing filed can
# distinguish from a bonus.
_MAX_RATIO_DENOMINATOR = 20
_RATIO_TOLERANCE = Decimal("1e-6")


def _clean_ratio(earlier: Decimal, later: Decimal) -> Decimal | None:
    """``later / earlier`` as a clean small rational, or ``None`` if it is dirty.

    Tested on whichever side of 1 the ratio falls, so a 1:100 grupamento (whose
    *numerator* is small) is found through its inverse.
    """
    if earlier <= 0 or later <= 0 or earlier == later:
        return None
    big, small = (later, earlier) if later > earlier else (earlier, later)
    for denominator in range(1, _MAX_RATIO_DENOMINATOR + 1):
        q = Decimal(denominator)
        p = (big * q / small).to_integral_value(rounding=ROUND_HALF_EVEN)
        if p <= q:
            continue
        if abs(big * q - small * p) / (big * q) <= _RATIO_TOLERANCE:
            ratio = p / q
            return ratio if later > earlier else 1 / ratio
    return None


def restatement_factors(
    issued_by_year: Mapping[int, Decimal],
) -> dict[int, Decimal]:
    """The factor that restates each year's counts onto the latest year's base.

    The closed-year per-share history is split-adjusted (ADR 0027): a year that
    predates a split/bonus/grupamento has its counts multiplied forward so the
    LPA/VPA series is continuous, and so the count pairs with the price series —
    Yahoo back-adjusts every close for splits, and an as-filed count against an
    adjusted price undercounted BBAS3's pre-bonus caps by exactly the bonus.

    Consecutive filed years whose ratio is *not* clean (a real issuance, a
    buyback cancellation) contribute factor 1: those shares moved between owners,
    and restating them would rewrite a dilution as a corporate action. The latest
    year is the base and always maps to 1.
    """
    factors: dict[int, Decimal] = {}
    running = Decimal(1)
    ordered = sorted(issued_by_year, reverse=True)
    for year, previous_year in zip(ordered, ordered[1:], strict=False):
        factors[year] = running
        ratio = _clean_ratio(issued_by_year[previous_year], issued_by_year[year])
        if ratio is not None:
            running *= ratio
    if ordered:
        factors[ordered[-1]] = running
    return factors
