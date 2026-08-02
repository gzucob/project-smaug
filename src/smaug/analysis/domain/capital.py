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

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
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


# How closely a composition split's post-action total must match the FRE year's
# count for the two to be the same event. They are near-simultaneous post-split
# readings, so the match is tight; a loose bound would let an unrelated level pass.
_COMPOSITION_MATCH_TOLERANCE = Decimal("0.005")


def _composition_split(
    units_series: Sequence[Decimal], later: Decimal
) -> Decimal | None:
    """A clean *share-increasing* action in the composition that explains ``later``.

    ADR 0028: a split approved between a fiscal year-end and the FRE's approval
    date lands in the wrong FRE year and combines with a same-year cancellation, so
    the FRE-year ratio comes out dirty (VIVT3 2024: 1.9734). The composition member
    is dated by the real quarter, so the split shows there as a clean ratio — but
    only where the member is filed **in units**: a thousands-scale row is rounded
    and its ratios cannot be exact to the share (LREN3's buyback would look like a
    clean 19/20). ``units_series`` is therefore the units-scale rows only, in date
    order.

    Returns the clean ratio of the one consecutive pair that both is a
    share-increasing corporate action (``> 1``, exact to the share) and lands on
    ``later`` — the FRE's post-jump count, the reliable anchor. A cancellation
    (ratio ``< 1``) never restates (ADR 0027). ``None`` unless exactly one pair
    qualifies, so an ambiguous series changes nothing.
    """
    matches = [
        ratio
        for earlier, post in zip(units_series, units_series[1:], strict=False)
        if (ratio := _clean_ratio(earlier, post)) is not None
        and ratio > 1
        and abs(post - later) / later <= _COMPOSITION_MATCH_TOLERANCE
    ]
    return matches[0] if len(matches) == 1 else None


# How closely a declared event's "before" count must match a filed year's count
# for the two to describe the same share base. They are the same quantity read
# from two members of one archive, so the match is exact in practice; the margin
# only absorbs a filer restating a rounded figure.
_DECLARED_MATCH_TOLERANCE = Decimal("1e-9")


@dataclass(frozen=True, slots=True)
class CorporateAction:
    """One split, grupamento or bonificação, as the company **declared** it.

    CVM files these outright (``fre_..._capital_social_desdobramento``) with the
    count on both sides of the approval, so the ratio is stated rather than
    inferred from how the count moved between two filings. The difference is what
    separates an action from everything else that happened in between: Ampla's
    count fell 1/23,539 across two FREs, of which the grupamento was 1/40,000 and
    the rest a share issue.
    """

    approval_date: str  # ISO ``YYYY-MM-DD``; sorts as filed
    kind: str  # Grupamento | Desdobramento | Bonificação
    total_before: Decimal
    total_after: Decimal

    @property
    def ratio(self) -> Decimal | None:
        """``after / before``, or ``None`` when either side was filed as zero."""
        if self.total_before <= 0 or self.total_after <= 0:
            return None
        return self.total_after / self.total_before


def _declared_step(
    actions: Sequence[CorporateAction],
    earlier: Decimal,
    later: Decimal,
    consumed: set[str],
) -> Decimal | None:
    """The compounded ratio of the declared actions between two filed counts.

    An action is matched to a share base by its own ``total_before``, not by its
    approval date. Dates cannot do this job: a split approved between a year-end
    and the FRE's own approval lands in the following year's filing (ADR 0028),
    so Ampla's December-2015 grupamento is absent from the 2015 *and* 2016 FREs.
    The count it started from is unambiguous where the date is not.

    **The chain runs on until it reaches ``later``**, because one filing step can
    hold more than one action. The FRE year lags what it reports: Bradesco's count
    moves ×1.21 between its 2015 and 2016 filings, and the two 10% bonuses that
    make it up were approved in March 2016 and March 2017. Stopping at the first
    takes 1.10 where 1.21 happened, and the shortfall compounds down the series.
    A composite action needs no special case under this rule — VIVT3's ×80 split
    and ×0.025 grupamento simply chain to the ×2 the market saw.

    It also stops when nothing further matches, which is what leaves an issuance
    out: Ampla's chain ends at the grupamento's 98,062,897 while the next filing
    says 166,634,326, and the shares in between were sold, not split.

    Each approval is spent once, so a count returning to a level some action
    started from cannot claim it a second time.
    """
    running = Decimal(1)
    base = earlier
    remaining = [
        action
        for action in actions
        if action.ratio is not None and action.approval_date not in consumed
    ]
    while True:
        for action in remaining:
            ratio = action.ratio
            if (
                ratio is not None
                and abs(action.total_before - base)
                <= abs(base) * _DECLARED_MATCH_TOLERANCE
            ):
                running *= ratio
                base = action.total_after
                consumed.add(action.approval_date)
                remaining.remove(action)
                break
        else:
            return running if running != 1 else None
        if abs(base - later) <= abs(later) * _DECLARED_MATCH_TOLERANCE:
            return running


def restatement_factors(
    issued_by_year: Mapping[int, Decimal],
    composition_units: Sequence[Decimal] = (),
    actions: Sequence[CorporateAction] = (),
) -> dict[int, Decimal]:
    """The factor that restates each year's counts onto the latest year's base.

    ``actions`` are the corporate actions the company **declared** to CVM, and
    they are consulted first: a declared ratio is a fact, while the ratio between
    two filed counts is a guess that also moves on issuances and cancellations.
    Where a year's count starts a declared action, that action's ratio is the
    step; the inference below is the fallback for the years and companies CVM's
    file does not reach (it stops after the 2023 FRE).


    The closed-year per-share history is split-adjusted (ADR 0027): a year that
    predates a split/bonus/grupamento has its counts multiplied forward so the
    LPA/VPA series is continuous, and so the count pairs with the price series —
    Yahoo back-adjusts every close for splits, and an as-filed count against an
    adjusted price undercounted BBAS3's pre-bonus caps by exactly the bonus.

    Consecutive filed years whose ratio is *not* clean (a real issuance, a
    buyback cancellation) contribute factor 1: those shares moved between owners,
    and restating them would rewrite a dilution as a corporate action. The latest
    year is the base and always maps to 1.

    ``composition_units`` — the units-scale composition totals in date order —
    recovers the split the FRE hides when it combines a split with a same-year
    cancellation (ADR 0028): a dirty but *share-increasing* FRE ratio is retried
    against the composition, where the split reads clean. It is only ever consulted
    for such ratios, so a clean FRE ratio (BBAS3, LREN3's bonus, SANEPAR) and a
    share-*decreasing* one (a cancellation) behave exactly as before.
    """
    factors: dict[int, Decimal] = {}
    running = Decimal(1)
    consumed: set[str] = set()
    ordered = sorted(issued_by_year, reverse=True)
    for year, previous_year in zip(ordered, ordered[1:], strict=False):
        factors[year] = running
        earlier, later = issued_by_year[previous_year], issued_by_year[year]
        ratio = None
        # A company that files the same count two years running had no action
        # between them, whatever it once declared — and a declared event whose
        # ``before`` still matches would otherwise be applied again at every
        # standstill year. EALT3 files 22.5 M unchanged from 2015, and its single
        # x10 split compounded nine times into 2.25e16 shares before this guard.
        if earlier != later:
            ratio = _declared_step(actions, earlier, later, consumed)
        if ratio is None:
            ratio = _clean_ratio(earlier, later)
            if ratio is None and later > earlier:
                ratio = _composition_split(composition_units, later)
        if ratio is not None:
            running *= ratio
    if ordered:
        factors[ordered[-1]] = running
    return factors
