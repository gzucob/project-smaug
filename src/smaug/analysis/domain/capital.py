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

from decimal import Decimal

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

    treasury = (
        composition.treasury_common,
        composition.treasury_preferred,
        composition.treasury_total,
    )
    # BBDC4's 2022 composition files a *negative* treasury count. A company cannot
    # hold fewer than none of its own shares, so the filing is not readable — and
    # subtracting it would silently *inflate* the company instead.
    if any(count is not None and count < 0 for count in treasury):
        return None

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
    """One class's outstanding count. A class the filer does not have stays ``None``."""
    if issued is None:
        return None
    return issued if treasury is None else issued - treasury * scale
