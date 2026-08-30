"""Prove the three known B3 roots reused by another CVM registrant.

This is a deliberately narrow repair for ``JBSS3``, ``PETZ3`` and ``MOAR3``.
It does not add a source or infer a predecessor from a root, name, price, or
date. The FCA supplies the predecessor registrant, the current B3 supplement
supplies the successor and its listing boundary, and COTAHIST supplies the
security identity on the event session.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import date

from smaug.ingestion.domain.ports import B3TapeEvidenceReader, B3TapeObservation
from smaug.ingestion.infrastructure.b3_listed_company import B3ListedCompany
from smaug.shared.errors import SourceError

# These are the only roots covered by this repair. A future reused root needs
# its own source evidence and must not be admitted by a generic mismatch rule.
REUSED_ROOT_PREDECESSORS = {
    "JBSS3": "20575",
    "PETZ3": "25089",
    "MOAR3": "8893",
}
REUSED_ROOT_SUCCESSORS = {
    "JBSS3": "80233",
    "PETZ3": "917942",
    "MOAR3": "917927",
}
REUSED_ROOT_TICKERS = frozenset(REUSED_ROOT_PREDECESSORS)


@dataclass(frozen=True, slots=True)
class B3ReusedRootProof:
    """Primary-source identity chain authorizing one predecessor security."""

    ticker: str
    predecessor_cvm_code: str
    predecessor_cnpj: str
    successor_cvm_code: str
    successor_issuing_company: str
    successor_trading_name: str
    security_isin: str
    security_especi: str
    security_bdi: str
    security_name: str
    security_tape_code: str
    predecessor_last_session: date
    successor_first_session: date

    @property
    def security_class(self) -> str:
        """The economic class named by COTAHIST's complete species field."""
        return _species_class(self.security_especi)

    def as_mapping(self) -> dict[str, object]:
        """Serialize the identity chain for raw request and payload provenance."""
        return {
            "sources": [
                "cvm_fca.registrant",
                "cvm_fca.security",
                "b3.listed_supplement.successor",
                "b3.cotahist.identity",
            ],
            "predecessor": {
                "cvm_code": self.predecessor_cvm_code,
                "cnpj": self.predecessor_cnpj,
                "ticker": self.ticker,
            },
            "successor": {
                "cvm_code": self.successor_cvm_code,
                "issuing_company": self.successor_issuing_company,
                "trading_name": self.successor_trading_name,
                "first_quoted_session": self.successor_first_session.isoformat(),
            },
            "security": {
                "ticker": self.ticker,
                "isin": self.security_isin,
                "especi": self.security_especi,
                "class": self.security_class,
                "bdi": self.security_bdi,
                "name": self.security_name,
                "tape_code": self.security_tape_code,
            },
            "boundary": {
                "predecessor_last_session": self.predecessor_last_session.isoformat(),
                "successor_first_session": self.successor_first_session.isoformat(),
            },
        }


@dataclass(frozen=True, slots=True)
class B3ReusedRootDecision:
    """Attribution decision for one B3 event row."""

    accepted: bool
    reason: str
    evidence: Mapping[str, object]


@dataclass(frozen=True, slots=True)
class B3ReusedRootProofResult:
    """Either a complete predecessor proof or a named quarantine reason."""

    proof: B3ReusedRootProof | None
    reason: str | None = None
    evidence: Mapping[str, object] = field(default_factory=dict)


class B3ReusedRootRecovery:
    """Build and apply the bounded predecessor proof for the known roots."""

    def __init__(
        self,
        *,
        ticker_to_code: Mapping[str, str],
        ticker_to_cnpj: Mapping[str, str],
        tape: B3TapeEvidenceReader,
    ) -> None:
        self._ticker_to_code = {
            ticker.strip().upper(): str(code).strip()
            for ticker, code in ticker_to_code.items()
        }
        self._ticker_to_cnpj = {
            ticker.strip().upper(): str(cnpj).strip()
            for ticker, cnpj in ticker_to_cnpj.items()
        }
        self._tape = tape

    def supports(self, ticker: str) -> bool:
        """Whether this targeted repair owns the requested security."""
        return ticker.strip().upper() in REUSED_ROOT_TICKERS

    async def prove(
        self, ticker: str, current: B3ListedCompany
    ) -> B3ReusedRootProofResult:
        """Prove the predecessor before admitting any event row."""
        code = ticker.strip().upper()
        expected_predecessor = REUSED_ROOT_PREDECESSORS.get(code)
        expected_successor = REUSED_ROOT_SUCCESSORS.get(code)
        if expected_predecessor is None or expected_successor is None:
            return self._failure("ticker-outside-targeted-repair", ticker=code)

        predecessor_code = self._ticker_to_code.get(code, "")
        predecessor_cnpj = self._ticker_to_cnpj.get(code, "")
        if not _same_code(predecessor_code, expected_predecessor):
            return self._failure(
                "predecessor-cvm-code-mismatch",
                ticker=code,
                expected=expected_predecessor,
                published=predecessor_code,
            )
        if not predecessor_cnpj:
            return self._failure("predecessor-cnpj-missing", ticker=code)

        successor_code = (current.cvm_code or "").strip()
        if not _same_code(successor_code, expected_successor):
            return self._failure(
                "successor-cvm-code-mismatch",
                ticker=code,
                expected=expected_successor,
                published=successor_code,
            )
        if _same_code(predecessor_code, successor_code):
            return self._failure(
                "registrant-boundary-missing",
                ticker=code,
                predecessor=predecessor_code,
                successor=successor_code,
            )

        successor_first = current.quotation_date
        if successor_first is None:
            return self._failure(
                "successor-boundary-missing",
                ticker=code,
                successor=successor_code,
            )
        if current.requested_root != code[:4] or current.issuing_company != code[:4]:
            return self._failure(
                "successor-root-mismatch",
                ticker=code,
                requested_root=current.requested_root,
                issuing_company=current.issuing_company,
            )

        try:
            latest = await self._tape.latest_before(code, successor_first)
        except SourceError as exc:
            return self._failure(
                "b3-cotahist-unavailable",
                ticker=code,
                detail=str(exc),
            )
        if latest is None:
            return self._failure(
                "predecessor-tape-missing",
                ticker=code,
                boundary=successor_first.isoformat(),
            )
        if latest.session >= successor_first:
            return self._failure(
                "registrant-boundary-unproven",
                ticker=code,
                predecessor_last=latest.session.isoformat(),
                successor_first=successor_first.isoformat(),
            )
        if not latest.isin or not latest.especi or not _species_class(latest.especi):
            return self._failure(
                "predecessor-security-identity-missing",
                ticker=code,
                session=latest.session.isoformat(),
                isin=latest.isin,
                especi=latest.especi,
            )

        proof = B3ReusedRootProof(
            ticker=code,
            predecessor_cvm_code=predecessor_code,
            predecessor_cnpj=predecessor_cnpj,
            successor_cvm_code=successor_code,
            successor_issuing_company=current.issuing_company,
            successor_trading_name=current.trading_name,
            security_isin=latest.isin,
            security_especi=latest.especi,
            security_bdi=latest.bdi,
            security_name=latest.name,
            security_tape_code=latest.code,
            predecessor_last_session=latest.session,
            successor_first_session=successor_first,
        )
        return B3ReusedRootProofResult(proof=proof, evidence=proof.as_mapping())

    async def capital_event(
        self, proof: B3ReusedRootProof, row: Mapping[str, object]
    ) -> B3ReusedRootDecision:
        """Authorize a stock event only on the predecessor's identified paper."""
        return await self._event(
            proof,
            row.get("lastDatePrior"),
            isin=row.get("isinCode"),
            share_class=None,
        )

    async def cash_dividend(
        self, proof: B3ReusedRootProof, row: Mapping[str, object]
    ) -> B3ReusedRootDecision:
        """Authorize cash only when its class is present on the old tape."""
        return await self._event(
            proof,
            row.get("lastDatePriorEx"),
            isin=None,
            share_class=row.get("typeStock"),
        )

    async def _event(
        self,
        proof: B3ReusedRootProof,
        raw_date: object,
        *,
        isin: object,
        share_class: object,
    ) -> B3ReusedRootDecision:
        session = _b3_date(raw_date)
        if session is None:
            return B3ReusedRootDecision(
                False,
                "event-date-unparseable",
                {"raw_date": _text(raw_date)},
            )
        if session >= proof.successor_first_session:
            return B3ReusedRootDecision(
                False,
                "successor-event-excluded",
                {
                    "event_session": session.isoformat(),
                    "successor_first_session": (
                        proof.successor_first_session.isoformat()
                    ),
                },
            )
        if session > proof.predecessor_last_session:
            return B3ReusedRootDecision(
                False,
                "event-after-predecessor-tape",
                {
                    "event_session": session.isoformat(),
                    "predecessor_last_session": (
                        proof.predecessor_last_session.isoformat()
                    ),
                },
            )

        try:
            observation = await self._tape.at(proof.ticker, session)
            if not _matches_security(observation, proof):
                observation = await self._legacy_identity(
                    session,
                    isin=proof.security_isin,
                    security_class=proof.security_class,
                )
        except SourceError as exc:
            return B3ReusedRootDecision(
                False,
                "b3-cotahist-unavailable",
                {"event_session": session.isoformat(), "detail": str(exc)},
            )
        if observation is None:
            return B3ReusedRootDecision(
                False,
                "predecessor-session-missing",
                {"event_session": session.isoformat()},
            )
        if observation.isin != proof.security_isin:
            return B3ReusedRootDecision(
                False,
                "security-isin-mismatch",
                {
                    "event_session": session.isoformat(),
                    "expected_isin": proof.security_isin,
                    "published_isin": observation.isin,
                },
            )
        if _species_class(observation.especi) != proof.security_class:
            return B3ReusedRootDecision(
                False,
                "security-species-mismatch",
                {
                    "event_session": session.isoformat(),
                    "expected_especi": proof.security_especi,
                    "published_especi": observation.especi,
                },
            )
        row_isin = _text(isin).upper()
        if row_isin and row_isin != observation.isin:
            return B3ReusedRootDecision(
                False,
                "event-isin-mismatch",
                {
                    "event_session": session.isoformat(),
                    "expected_isin": observation.isin,
                    "published_isin": row_isin,
                },
            )
        row_class = _text(share_class).upper()
        if row_class and row_class != proof.security_class:
            return B3ReusedRootDecision(
                False,
                "event-share-class-mismatch",
                {
                    "event_session": session.isoformat(),
                    "expected_class": proof.security_class,
                    "published_class": row_class,
                },
            )
        return B3ReusedRootDecision(
            True,
            "predecessor-identity-confirmed",
            {
                "event_session": session.isoformat(),
                "isin": observation.isin,
                "especi": observation.especi,
                "bdi": observation.bdi,
                "tape_code": observation.code,
            },
        )

    async def _legacy_identity(
        self,
        session: date,
        *,
        isin: str,
        security_class: str,
    ) -> B3TapeObservation | None:
        return await self._tape.by_identity(
            session, isin=isin, security_class=security_class
        )

    @staticmethod
    def _failure(reason: str, **evidence: object) -> B3ReusedRootProofResult:
        return B3ReusedRootProofResult(
            proof=None,
            reason=reason,
            evidence=evidence,
        )


def _same_code(left: str, right: str) -> bool:
    return (left.lstrip("0") or "0") == (right.lstrip("0") or "0")


def _species_class(value: str) -> str:
    """Extract the class token while retaining the complete field separately."""
    return value.strip().upper().split(maxsplit=1)[0] if value.strip() else ""


def _matches_security(observation: object, proof: B3ReusedRootProof) -> bool:
    return (
        observation is not None
        and getattr(observation, "isin", "") == proof.security_isin
        and _species_class(str(getattr(observation, "especi", "")))
        == proof.security_class
    )


def _b3_date(value: object) -> date | None:
    text = _text(value)
    if not text:
        return None
    try:
        if "/" in text:
            day, month, year = (int(part) for part in text.split("/", 2))
            return date(year, month, day)
        return date.fromisoformat(text[:10])
    except ValueError:
        return None


def _text(value: object) -> str:
    return str(value).strip() if value is not None else ""
