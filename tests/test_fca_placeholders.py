"""Official FCA placeholder recovery: class, window and collision gates."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace
from datetime import date
from decimal import Decimal

from smaug.analysis.domain.financials import SessionClose
from smaug.ingestion.infrastructure.b3_listed_company import (
    B3CompanyResolutionError,
)
from smaug.portfolio.domain.company import InstrumentKind
from smaug.portfolio.domain.fca_placeholders import (
    FcaCodeIssue,
    FcaPlaceholderRow,
    FcaRecoveryStatus,
)
from smaug.portfolio.domain.share_classes import PerShareClass, UnitComponent
from smaug.portfolio.infrastructure.fca_placeholders import (
    FcaPlaceholderRecovery,
    OfficialRegistrant,
    OfficialSecurityCode,
    QuoteClose,
    QuoteIdentity,
    QuoteSeries,
)


@dataclass(frozen=True)
class _Identity:
    isin: str
    especi: str


class _Quote:
    def __init__(self, session: date, *, isin: str, especi: str) -> None:
        self._close = SessionClose(session=session, close=Decimal(1))
        self._identity = _Identity(isin=isin, especi=especi)

    def session_closes(self) -> Sequence[QuoteClose]:
        return (self._close,)

    def identity_at(self, _session: date) -> QuoteIdentity:
        return self._identity


class _Archive:
    def __init__(self, quotes: Mapping[str, QuoteSeries]) -> None:
        self._quotes = quotes

    async def year(self, _year: int) -> Mapping[str, QuoteSeries]:
        return self._quotes


class _Resolver:
    def __init__(self, company: OfficialRegistrant | Exception) -> None:
        self._company = company

    async def resolve_by_cvm(
        self, _cvm: str, *, cnpj: str | None = None
    ) -> OfficialRegistrant:
        if isinstance(self._company, Exception):
            raise self._company
        assert cnpj is not None
        assert self._company.cnpj == cnpj
        return self._company


def _row(
    *,
    number: int,
    cnpj: str,
    code: str,
    kind: InstrumentKind,
    per_share_class: PerShareClass | None = None,
    components: tuple[UnitComponent, ...] = (),
) -> FcaPlaceholderRow:
    return FcaPlaceholderRow(
        row_number=number,
        cnpj=cnpj,
        cd_cvm="123",
        denom="TEST S.A.",
        raw_code=code,
        code_issue=FcaCodeIssue.BLANK if not code else FcaCodeIssue.MALFORMED,
        instrument_kind=kind,
        instrument_type=(
            "Units" if kind is InstrumentKind.UNIT else "Ações Ordinárias"
        ),
        listed_since=date(2026, 1, 1),
        per_share_class=per_share_class,
        unit_components=components,
        shares_per_unit=sum(component.quantity for component in components) or None,
    )


async def test_unit_uses_official_codes_and_cotahist_class_evidence() -> None:
    company = OfficialRegistrant(
        cvm_code="123",
        cnpj="12.000.000/0001-00",
        issuing_company="BPAC",
        security_codes=(
            OfficialSecurityCode("BPAC11", "BRBPACUNT006"),
            OfficialSecurityCode("BPAC3", "BRBPACACNOR7"),
            OfficialSecurityCode("BPAC5", "BRBPACNPA0"),
        ),
    )
    archive = _Archive(
        {
            code: _Quote(date(2026, 2, 2), isin=isin, especi=especi)
            for code, isin, especi in (
                ("BPAC3", "BRBPACACNOR7", "ON"),
                ("BPAC5", "BRBPACNPA0", "PNA"),
                ("BPAC11", "BRBPACUNT006", "UNT"),
            )
        }
    )
    row = _row(
        number=2,
        cnpj=company.cnpj or "",
        code="000000",
        kind=InstrumentKind.UNIT,
        components=(
            UnitComponent(1, PerShareClass.ORDINARY),
            UnitComponent(2, PerShareClass.PREFERRED_A),
        ),
    )

    result = await FcaPlaceholderRecovery(
        _Resolver(company), archive, snapshot_year=2026, today=date(2026, 8, 1)
    ).recover((row,))

    assert result.report.recovered[0].recovered_codes == ("BPAC11", "BPAC3", "BPAC5")
    assert {identity.ticker for identity in result.identities} == {
        "BPAC3",
        "BPAC5",
        "BPAC11",
    }
    unit = next(
        identity for identity in result.identities if identity.ticker == "BPAC11"
    )
    assert {component.symbol for component in unit.unit_components} == {
        "BPAC3",
        "BPAC5",
    }


async def test_non_unit_selects_the_class_matching_the_fca_label() -> None:
    company = OfficialRegistrant(
        cvm_code="123",
        cnpj="12.000.000/0001-00",
        issuing_company="ABCD",
        security_codes=(
            OfficialSecurityCode("ABCD3", "BRABCDACNOR0"),
            OfficialSecurityCode("ABCD5", "BRABCDNPA0"),
        ),
    )
    row = _row(
        number=2,
        cnpj=company.cnpj or "",
        code="",
        kind=InstrumentKind.COMMON_SHARE,
        per_share_class=PerShareClass.ORDINARY,
    )
    result = await FcaPlaceholderRecovery(
        _Resolver(company),
        _Archive({"ABCD3": _Quote(date(2026, 2, 2), isin="BRABCDACNOR0", especi="ON")}),
        snapshot_year=2026,
        today=date(2026, 8, 1),
    ).recover((row,))

    assert result.report.recovered[0].recovered_codes == ("ABCD3",)


async def test_preferred_without_subclass_evidence_stays_ambiguous() -> None:
    company = OfficialRegistrant(
        cvm_code="123",
        cnpj="12.000.000/0001-00",
        issuing_company="ABCD",
        security_codes=(
            OfficialSecurityCode("ABCD4", "BRABCDPN0"),
            OfficialSecurityCode("ABCD5", "BRABCDPNA0"),
        ),
    )
    row = _row(
        number=2,
        cnpj=company.cnpj or "",
        code="000000",
        kind=InstrumentKind.PREFERRED_SHARE,
        per_share_class=PerShareClass.PREFERRED,
    )
    result = await FcaPlaceholderRecovery(
        _Resolver(company),
        _Archive(
            {
                "ABCD4": _Quote(date(2026, 2, 2), isin="BRABCDPN0", especi="PN"),
                "ABCD5": _Quote(date(2026, 2, 2), isin="BRABCDPNA0", especi="PNA"),
            }
        ),
        snapshot_year=2026,
        today=date(2026, 8, 1),
    ).recover((row,))

    assert result.report.unresolved[0].reason == "ambiguous-cotahist-code"
    assert result.identities == ()


async def test_reused_official_code_is_rejected_for_both_registrants() -> None:
    code = OfficialSecurityCode("ABCD3", "BRABCDACNOR0")
    first = OfficialRegistrant("123", "12.000.000/0001-00", "REUS", (code,))
    second = OfficialRegistrant("456", "45.000.000/0001-00", "REUS", (code,))

    class _TwoRegistrants(_Resolver):
        async def resolve_by_cvm(
            self, cvm: str, *, cnpj: str | None = None
        ) -> OfficialRegistrant:
            return first if cvm == "123" else second

    rows: tuple[FcaPlaceholderRow, ...] = (
        _row(
            number=2,
            cnpj=first.cnpj or "",
            code="",
            kind=InstrumentKind.COMMON_SHARE,
            per_share_class=PerShareClass.ORDINARY,
        ),
        _row(
            number=3,
            cnpj=second.cnpj or "",
            code="",
            kind=InstrumentKind.COMMON_SHARE,
            per_share_class=PerShareClass.ORDINARY,
        ),
    )
    rows = tuple(
        row if row.row_number == 2 else replace(row, cd_cvm="456") for row in rows
    )
    result = await FcaPlaceholderRecovery(
        _TwoRegistrants(first),
        _Archive({"ABCD3": _Quote(date(2026, 2, 2), isin="BRABCDACNOR0", especi="ON")}),
        snapshot_year=2026,
        today=date(2026, 8, 1),
    ).recover(rows)

    assert all(
        finding.status is FcaRecoveryStatus.UNRESOLVED
        for finding in result.report.findings
    )
    assert all(
        finding.reason == "b3-code-collision" for finding in result.report.findings
    )
    assert result.identities == ()


async def test_b3_endpoint_failure_is_an_explicit_unresolved_finding() -> None:
    row = _row(
        number=2,
        cnpj="12.000.000/0001-00",
        code="",
        kind=InstrumentKind.COMMON_SHARE,
        per_share_class=PerShareClass.ORDINARY,
    )
    failure = B3CompanyResolutionError("coverage-established", "endpoint down")
    result = await FcaPlaceholderRecovery(
        _Resolver(failure), _Archive({}), snapshot_year=2026
    ).recover((row,))

    assert result.report.unresolved[0].reason == "b3-coverage-established"
    assert "endpoint down" in (result.report.unresolved[0].detail or "")
