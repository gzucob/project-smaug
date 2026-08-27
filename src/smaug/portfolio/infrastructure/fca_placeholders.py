"""Official recovery of FCA rows with unusable trading codes.

This module owns the portfolio identity policy.  Its two source boundaries are
small structural protocols so the composition root can reuse the existing B3
listed-company resolver and COTAHIST reader without making this context import
either implementation.

The permitted chain is ``FCA CNPJ/CD_CVM -> official registrant -> official
security code -> COTAHIST class/window``.  A name, root, price, isolated ISIN or
familiar symbol never authorizes an identity.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace
from datetime import date
from typing import Protocol

from smaug.portfolio.domain.company import CompanyIdentity, InstrumentKind
from smaug.portfolio.domain.fca_placeholders import (
    FcaPlaceholderFinding,
    FcaPlaceholderReport,
    FcaPlaceholderRow,
    FcaRecoveryResult,
    FcaRecoveryStatus,
)
from smaug.portfolio.domain.share_classes import (
    PerShareClass,
    ShareClass,
    ShareKind,
    TickerCodeEvidence,
    UnitComponent,
    mapping_for_share_class,
)
from smaug.portfolio.domain.universe import is_trading_code
from smaug.shared.errors import SourceError


@dataclass(frozen=True, slots=True)
class OfficialSecurityCode:
    """A security code and optional ISIN published by the official registrant."""

    code: str
    isin: str | None = None


@dataclass(frozen=True, slots=True)
class OfficialRegistrant:
    """B3 registrant evidence reached from an FCA CD_CVM/CNPJ pair."""

    cvm_code: str
    cnpj: str | None
    issuing_company: str
    security_codes: tuple[OfficialSecurityCode, ...]
    quotation_date: date | None = None
    market: str | None = None
    venue: str | None = None


class RegistrantResolver(Protocol):
    """The official registrant lookup used by the portfolio recovery policy."""

    async def resolve_by_cvm(
        self, cvm_code: str, *, cnpj: str | None = None
    ) -> OfficialRegistrant: ...


OfficialRegistrantResolver = RegistrantResolver


class QuoteClose(Protocol):
    """The date-bearing part of one COTAHIST session close."""

    @property
    def session(self) -> date: ...


class QuoteIdentity(Protocol):
    """Identity fields retained by the COTAHIST reduction."""

    @property
    def isin(self) -> str: ...

    @property
    def especi(self) -> str: ...


class QuoteSeries(Protocol):
    """The COTAHIST evidence needed for a code/window check."""

    def session_closes(self) -> Sequence[QuoteClose]: ...

    def identity_at(self, session: date) -> QuoteIdentity | None: ...


class QuoteArchive(Protocol):
    """Yearly COTAHIST source boundary."""

    async def year(self, year: int) -> Mapping[str, QuoteSeries]: ...


_EVIDENCE = (
    "cvm_fca.placeholder",
    "b3.get_detail",
    "b3.listed_supplement",
    "b3.cotahist",
)
_FUNDAMENTAL = frozenset(
    {
        InstrumentKind.COMMON_SHARE,
        InstrumentKind.PREFERRED_SHARE,
        InstrumentKind.UNIT,
    }
)


@dataclass(frozen=True, slots=True)
class _Candidate:
    code: str
    isin: str | None


@dataclass(frozen=True, slots=True)
class _Observation:
    code: str
    observed: bool
    accepted: bool
    reason: str
    detail: str | None = None


@dataclass(frozen=True, slots=True)
class _Accepted:
    row: FcaPlaceholderRow
    company: OfficialRegistrant
    candidate: _Candidate
    finding: FcaPlaceholderFinding


class FcaPlaceholderRecovery:
    """Recover only registrant-verified codes observed in B3's tape."""

    def __init__(
        self,
        resolver: RegistrantResolver,
        archive: QuoteArchive,
        *,
        snapshot_year: int,
        today: date | None = None,
    ) -> None:
        self._resolver = resolver
        self._archive = archive
        self._snapshot_year = snapshot_year
        self._today = today or date.today()

    async def __call__(self, rows: tuple[FcaPlaceholderRow, ...]) -> FcaRecoveryResult:
        return await self.recover(rows)

    async def recover(self, rows: tuple[FcaPlaceholderRow, ...]) -> FcaRecoveryResult:
        findings: list[FcaPlaceholderFinding] = []
        accepted: list[_Accepted] = []
        registrants: dict[tuple[str, str], OfficialRegistrant | Exception] = {}
        for row in rows:
            if row.instrument_kind not in _FUNDAMENTAL:
                findings.append(
                    _finding(
                        row,
                        "instrument-not-fundamental",
                        detail=row.instrument_type or row.instrument_kind.value,
                    )
                )
                continue
            if not row.cnpj or not row.cd_cvm:
                findings.append(_finding(row, "missing-official-registrant-key"))
                continue
            key = (row.cd_cvm, row.cnpj)
            result = registrants.get(key)
            if result is None:
                try:
                    result = await self._resolver.resolve_by_cvm(
                        row.cd_cvm, cnpj=row.cnpj
                    )
                except Exception as exc:  # source adapters expose typed failures
                    result = exc
                registrants[key] = result
            if isinstance(result, Exception):
                findings.append(
                    _finding(
                        row,
                        _registrant_failure(result),
                        detail=str(result),
                        evidence=("cvm_fca.placeholder", "b3.get_detail"),
                    )
                )
                continue
            row_finding, candidate = await self._recover_row(row, result)
            findings.append(row_finding)
            if candidate is not None:
                accepted.append(_Accepted(row, result, candidate, row_finding))

        findings, accepted = _reject_collisions(findings, accepted)
        return FcaRecoveryResult(
            identities=_identities(accepted),
            report=FcaPlaceholderReport(
                snapshot_year=self._snapshot_year, findings=tuple(findings)
            ),
        )

    async def _recover_row(
        self, row: FcaPlaceholderRow, company: OfficialRegistrant
    ) -> tuple[FcaPlaceholderFinding, _Candidate | None]:
        start, end = _window(row, company, self._today)
        official = tuple(
            _Candidate(item.code, item.isin)
            for item in company.security_codes
            if is_trading_code(item.code)
        )
        candidates = _for_row(row, official)
        probes = candidates
        if row.instrument_kind is InstrumentKind.UNIT:
            probes = tuple(
                dict.fromkeys((*candidates, *_for_components(row, official)))
            )
        candidate_codes = tuple(sorted({candidate.code for candidate in official}))
        if start is not None and end is not None and start > end:
            return (
                _finding(
                    row,
                    "invalid-listing-window",
                    candidate_codes=candidate_codes,
                    official_root=company.issuing_company,
                    window_start=start,
                    window_end=end,
                ),
                None,
            )
        if not candidates:
            return (
                _finding(
                    row,
                    "no-official-security-code",
                    candidate_codes=candidate_codes,
                    official_root=company.issuing_company,
                    window_start=start,
                    window_end=end,
                ),
                None,
            )
        observations = [
            await self._observe(
                candidate,
                _probe_row(row, candidate),
                start=start,
                end=end,
            )
            for candidate in probes
        ]
        observed_codes = tuple(
            sorted(
                observation.code for observation in observations if observation.observed
            )
        )
        matches = tuple(
            candidate
            for candidate, observation in zip(probes, observations, strict=True)
            if candidate in candidates and observation.accepted
        )
        if len(matches) != 1:
            detail = (
                "; ".join(
                    f"{observation.code}: {observation.detail or observation.reason}"
                    for observation in observations
                    if observation.reason != "observed"
                )
                or None
            )
            reason = (
                "ambiguous-cotahist-code"
                if len(matches) > 1
                else _observation_failure(observations)
            )
            return (
                _finding(
                    row,
                    reason,
                    candidate_codes=candidate_codes,
                    official_root=company.issuing_company,
                    window_start=start,
                    window_end=end,
                    observed_codes=observed_codes,
                    detail=detail,
                ),
                None,
            )
        recovered_codes = tuple(
            sorted(
                candidate.code
                for candidate, observation in zip(probes, observations, strict=True)
                if observation.accepted
            )
        )
        return (
            _finding(
                row,
                "observed-in-cotahist",
                status=FcaRecoveryStatus.RECOVERED,
                candidate_codes=candidate_codes,
                official_root=company.issuing_company,
                window_start=start,
                window_end=end,
                observed_codes=observed_codes,
                recovered_codes=recovered_codes,
            ),
            matches[0],
        )

    async def _observe(
        self,
        candidate: _Candidate,
        row: FcaPlaceholderRow,
        *,
        start: date | None,
        end: date | None,
    ) -> _Observation:
        identity_isins: set[str] = set()
        identity_kinds: set[str] = set()
        closes_seen = False
        identity_seen = False
        try:
            for year in _years(start, end, self._snapshot_year):
                quote = (await self._archive.year(year)).get(candidate.code)
                if quote is None:
                    continue
                for close in quote.session_closes():
                    if not _in_window(close.session, start, end):
                        continue
                    closes_seen = True
                    state = quote.identity_at(close.session)
                    if state is None:
                        continue
                    isin = state.isin.strip().upper() if state.isin else ""
                    kind = _state_kind(state.especi) if state.especi else None
                    # A price row alone is not identity evidence.  Both the
                    # COTAHIST ISIN and its class marker must be present in
                    # the same session before the candidate can be accepted.
                    if not isin or kind is None:
                        continue
                    identity_seen = True
                    identity_isins.add(isin)
                    identity_kinds.add(kind)
        except (SourceError, OSError, ValueError, KeyError, IndexError) as exc:
            return _Observation(
                candidate.code,
                closes_seen,
                False,
                "cotahist-unavailable",
                str(exc),
            )
        if not closes_seen:
            return _Observation(candidate.code, False, False, "not-observed")
        if not identity_seen:
            return _Observation(
                candidate.code,
                True,
                False,
                "cotahist-identity-missing",
            )
        expected = _expected_kind(row)
        if identity_kinds and expected not in identity_kinds:
            return _Observation(
                candidate.code,
                True,
                False,
                "cotahist-class-mismatch",
                ",".join(sorted(identity_kinds)),
            )
        if candidate.isin and identity_isins:
            if identity_isins != {candidate.isin.upper()}:
                return _Observation(
                    candidate.code,
                    True,
                    False,
                    "cotahist-isin-mismatch",
                    ",".join(sorted(identity_isins)),
                )
        elif len(identity_isins) > 1:
            return _Observation(
                candidate.code,
                True,
                False,
                "cotahist-identity-collision",
                ",".join(sorted(identity_isins)),
            )
        return _Observation(candidate.code, True, True, "observed")


def _finding(
    row: FcaPlaceholderRow,
    reason: str,
    *,
    status: FcaRecoveryStatus = FcaRecoveryStatus.UNRESOLVED,
    candidate_codes: tuple[str, ...] = (),
    observed_codes: tuple[str, ...] = (),
    recovered_codes: tuple[str, ...] = (),
    official_root: str | None = None,
    window_start: date | None = None,
    window_end: date | None = None,
    evidence: tuple[str, ...] = _EVIDENCE,
    detail: str | None = None,
) -> FcaPlaceholderFinding:
    return FcaPlaceholderFinding(
        row=row,
        status=status,
        reason=reason,
        candidate_codes=candidate_codes,
        observed_codes=observed_codes,
        recovered_codes=recovered_codes,
        official_root=official_root,
        window_start=window_start,
        window_end=window_end,
        evidence=evidence,
        detail=detail,
    )


def _registrant_failure(error: Exception) -> str:
    code = getattr(error, "code", None)
    return f"b3-{code}" if isinstance(code, str) and code else "b3-resolution-failed"


def _for_row(
    row: FcaPlaceholderRow, candidates: Sequence[_Candidate]
) -> tuple[_Candidate, ...]:
    expected = row.per_share_class
    result: list[_Candidate] = []
    for candidate in candidates:
        suffix = candidate.code[4:]
        if row.instrument_kind is InstrumentKind.UNIT:
            if suffix == "11":
                result.append(candidate)
        elif row.instrument_kind is InstrumentKind.COMMON_SHARE:
            if suffix == "3":
                result.append(candidate)
        elif expected is PerShareClass.PREFERRED_A and suffix == "5":
            result.append(candidate)
        elif expected is PerShareClass.PREFERRED_B and suffix == "6":
            result.append(candidate)
        elif expected in (None, PerShareClass.PREFERRED) and suffix in {
            "4",
            "5",
            "6",
        }:
            result.append(candidate)
    return tuple(result)


def _for_components(
    row: FcaPlaceholderRow, candidates: Sequence[_Candidate]
) -> tuple[_Candidate, ...]:
    result: list[_Candidate] = []
    for component in row.unit_components:
        matching = tuple(
            candidate
            for candidate in candidates
            if _per_class(candidate.code) is component.per_share_class
        )
        if len(matching) == 1:
            result.append(matching[0])
    return tuple(result)


def _probe_row(row: FcaPlaceholderRow, candidate: _Candidate) -> FcaPlaceholderRow:
    suffix = candidate.code[4:]
    if suffix == "3":
        return replace(
            row,
            instrument_kind=InstrumentKind.COMMON_SHARE,
            per_share_class=PerShareClass.ORDINARY,
        )
    if suffix in {"4", "5", "6"}:
        return replace(
            row,
            instrument_kind=InstrumentKind.PREFERRED_SHARE,
            per_share_class=_per_class(candidate.code),
        )
    return row


def _per_class(code: str) -> PerShareClass:
    suffix = code[4:]
    if suffix == "3":
        return PerShareClass.ORDINARY
    if suffix == "5":
        return PerShareClass.PREFERRED_A
    if suffix == "6":
        return PerShareClass.PREFERRED_B
    return PerShareClass.PREFERRED


def _expected_kind(row: FcaPlaceholderRow) -> str:
    if row.instrument_kind is InstrumentKind.UNIT:
        return "UNIT"
    if row.instrument_kind is InstrumentKind.COMMON_SHARE:
        return "COMMON"
    return "PREFERRED"


def _state_kind(especi: str) -> str | None:
    token = especi.strip().upper().split(maxsplit=1)[0] if especi.strip() else ""
    if token.startswith("UNT") or token.startswith("UNIT"):
        return "UNIT"
    if token.startswith("ON") or token.startswith("ORD"):
        return "COMMON"
    if token.startswith("PN") or token.startswith("PREF"):
        return "PREFERRED"
    return None


def _observation_failure(observations: Sequence[_Observation]) -> str:
    reasons = {observation.reason for observation in observations}
    if "cotahist-unavailable" in reasons:
        return "cotahist-unavailable"
    if "cotahist-identity-missing" in reasons:
        return "cotahist-identity-missing"
    if "cotahist-class-mismatch" in reasons:
        return "cotahist-class-mismatch"
    if "cotahist-isin-mismatch" in reasons or "cotahist-identity-collision" in reasons:
        return "cotahist-identity-collision"
    return "code-not-observed-in-cotahist"


def _window(
    row: FcaPlaceholderRow, company: OfficialRegistrant, today: date
) -> tuple[date | None, date | None]:
    return row.listed_since or company.quotation_date, row.trading_ended or today


def _years(start: date | None, end: date | None, snapshot_year: int) -> range:
    first = start.year if start is not None else snapshot_year
    last = end.year if end is not None else snapshot_year
    return range(min(first, last), max(first, last) + 1)


def _in_window(session: date, start: date | None, end: date | None) -> bool:
    return (start is None or session >= start) and (end is None or session <= end)


def _reject_collisions(
    findings: list[FcaPlaceholderFinding], accepted: list[_Accepted]
) -> tuple[list[FcaPlaceholderFinding], list[_Accepted]]:
    claims: dict[str, set[str]] = {}
    for item in accepted:
        for code in item.finding.recovered_codes:
            claims.setdefault(code, set()).add(item.row.cnpj)
    collisions = {code for code, owners in claims.items() if len(owners) > 1}
    if not collisions:
        return findings, accepted
    rejected = {
        item.row.row_number
        for item in accepted
        if collisions.intersection(item.finding.recovered_codes)
    }
    updated = [
        finding.unresolved(
            "b3-code-collision",
            detail=", ".join(sorted(collisions.intersection(finding.recovered_codes))),
        )
        if finding.row.row_number in rejected
        else finding
        for finding in findings
    ]
    return updated, [item for item in accepted if item.row.row_number not in rejected]


def _identities(accepted: Sequence[_Accepted]) -> tuple[CompanyIdentity, ...]:
    unique: dict[str, _Accepted] = {}
    for item in accepted:
        for code in item.finding.recovered_codes:
            unique.setdefault(code, item)
    by_cnpj: dict[str, list[_Accepted]] = {}
    for item in unique.values():
        by_cnpj.setdefault(item.row.cnpj, []).append(item)
    identities: list[CompanyIdentity] = []
    for code, item in unique.items():
        row = item.row
        company_codes = tuple(
            sorted(
                {
                    sibling_code
                    for sibling in by_cnpj[row.cnpj]
                    for sibling_code in sibling.finding.recovered_codes
                }
            )
        )
        classes = tuple(
            ShareClass(
                symbol=class_code,
                kind=(
                    ShareKind.COMMON if class_code[4:] == "3" else ShareKind.PREFERRED
                ),
            )
            for class_code in company_codes
            if class_code[4:] in {"3", "4", "5", "6"}
        )
        components = _components(row, company_codes)
        mappings = tuple(
            mapping_for_share_class(
                row.cnpj,
                share_class,
                code_evidence=(
                    TickerCodeEvidence(
                        symbol=share_class.symbol,
                        source="b3_get_detail",
                    ),
                ),
            )
            for share_class in classes
        )
        is_primary = code == item.candidate.code
        identities.append(
            CompanyIdentity(
                ticker=code,
                cd_cvm=row.cd_cvm or item.company.cvm_code,
                cnpj=row.cnpj,
                denom=row.denom,
                cvm_sector=row.cvm_sector,
                situation=row.situation,
                instrument_kind=(
                    row.instrument_kind
                    if is_primary
                    else _instrument_kind_for_code(code)
                ),
                instrument_type=(
                    row.instrument_type
                    if is_primary
                    else _instrument_type_for_code(code)
                ),
                market=item.company.market or row.market,
                venue=item.company.venue or row.venue,
                listing_evidence=tuple(
                    dict.fromkeys((*item.finding.evidence, "cvm_fca.market"))
                ),
                trading_ended=row.trading_ended,
                listed_since=row.listed_since,
                share_classes=classes,
                shares_per_unit=row.shares_per_unit,
                unit_components=components,
                share_class_mappings=mappings,
            )
        )
    return tuple(sorted(identities, key=lambda identity: identity.ticker))


def _components(
    row: FcaPlaceholderRow, company_codes: Sequence[str]
) -> tuple[UnitComponent, ...]:
    if row.instrument_kind is not InstrumentKind.UNIT:
        return ()
    symbols = {
        _per_class(code): code
        for code in company_codes
        if code[4:] in {"3", "4", "5", "6"}
    }
    return tuple(
        replace(component, symbol=symbols.get(component.per_share_class))
        for component in row.unit_components
    )


def _instrument_kind_for_code(code: str) -> InstrumentKind:
    if code[4:] == "3":
        return InstrumentKind.COMMON_SHARE
    if code[4:] in {"4", "5", "6"}:
        return InstrumentKind.PREFERRED_SHARE
    return InstrumentKind.UNIT


def _instrument_type_for_code(code: str) -> str:
    if code[4:] == "3":
        return "Ações Ordinárias"
    if code[4:] in {"4", "5", "6"}:
        return "Ações Preferenciais"
    return "Units"
