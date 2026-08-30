"""Targeted recovery of B3 events across the three reused roots."""

from __future__ import annotations

import base64
import json
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date

import httpx
import pytest

from smaug.ingestion.domain.ports import B3TapeObservation
from smaug.ingestion.domain.validation import SourceBatchValidation
from smaug.ingestion.infrastructure.b3_capital_events import (
    CAPITAL_EVENT_B3_MODULE,
    B3CapitalEventSource,
)
from smaug.ingestion.infrastructure.b3_cash_dividends import (
    CASH_DIVIDEND_B3_MODULE,
    B3CashDividendSource,
)
from smaug.ingestion.infrastructure.b3_listed_company import B3ListedCompany
from smaug.ingestion.infrastructure.b3_reused_roots import (
    REUSED_ROOT_PREDECESSORS,
    REUSED_ROOT_SUCCESSORS,
    B3ReusedRootRecovery,
)
from smaug.shared.errors import SourceBatchValidationError

_CNPJS = {
    "JBSS3": "02.916.265/0001-60",
    "PETZ3": "18.328.118/0001-09",
    "MOAR3": "33.102.476/0001-92",
}
_ROOTS = {ticker: ticker[:4] for ticker in REUSED_ROOT_PREDECESSORS}
_ISINS = {
    "JBSS3": "BRJBSSACNOR8",
    "PETZ3": "BRPETZACNOR2",
    "MOAR3": "BRMOARACNOR5",
}
_BOUNDARIES = {
    "JBSS3": date(2025, 6, 9),
    "PETZ3": date(2026, 5, 21),
    "MOAR3": date(2026, 5, 15),
}
_LAST_SESSIONS = {
    "JBSS3": date(2025, 6, 6),
    "PETZ3": date(2026, 1, 2),
    "MOAR3": date(2026, 1, 29),
}
_EVENT_SESSIONS = {
    "JBSS3": date(2025, 6, 6),
    "PETZ3": date(2024, 11, 13),
    "MOAR3": date(2021, 12, 17),
}


@dataclass(frozen=True)
class _Tape:
    latest: Mapping[str, B3TapeObservation]
    sessions: Mapping[tuple[str, date], B3TapeObservation]
    legacy: B3TapeObservation | None = None

    async def latest_before(
        self, ticker: str, _session: date
    ) -> B3TapeObservation | None:
        return self.latest.get(ticker)

    async def at(self, ticker: str, session: date) -> B3TapeObservation | None:
        return self.sessions.get((ticker, session))

    async def by_identity(
        self, session: date, *, isin: str, security_class: str
    ) -> B3TapeObservation | None:
        for observation in self.sessions.values():
            if (
                observation.session <= session
                and observation.isin == isin
                and observation.especi.split(maxsplit=1)[0] == security_class
            ):
                return observation
        if (
            self.legacy is not None
            and self.legacy.session <= session
            and self.legacy.isin == isin
            and self.legacy.especi.split(maxsplit=1)[0] == security_class
        ):
            return self.legacy
        return None


class _Reporter:
    def __init__(self) -> None:
        self.reports: list[SourceBatchValidation] = []

    async def record(self, validation: SourceBatchValidation) -> None:
        self.reports.append(validation)


def _company(ticker: str) -> B3ListedCompany:
    root = _ROOTS[ticker]
    return B3ListedCompany(
        requested_root=root,
        issuing_company=root,
        trading_name=root,
        cvm_code=REUSED_ROOT_SUCCESSORS[ticker],
        supplement={"code": root, "codeCVM": REUSED_ROOT_SUCCESSORS[ticker]},
        quotation_date=_BOUNDARIES[ticker],
    )


def _recovery(ticker: str) -> B3ReusedRootRecovery:
    observation = B3TapeObservation(
        session=_LAST_SESSIONS[ticker],
        isin=_ISINS[ticker],
        especi="ON NM",
        bdi="02",
        name=ticker[:4],
        code=ticker,
    )
    return B3ReusedRootRecovery(
        ticker_to_code={ticker: REUSED_ROOT_PREDECESSORS[ticker]},
        ticker_to_cnpj={ticker: _CNPJS[ticker]},
        tape=_Tape(
            latest={ticker: observation},
            sessions={
                (ticker, _LAST_SESSIONS[ticker]): observation,
                (ticker, _EVENT_SESSIONS[ticker]): observation,
            },
        ),
    )


@pytest.mark.parametrize("ticker", ["JBSS3", "PETZ3", "MOAR3"])
async def test_all_three_predecessors_have_a_complete_identity_chain(
    ticker: str,
) -> None:
    result = await _recovery(ticker).prove(ticker, _company(ticker))

    assert result.proof is not None
    assert result.proof.predecessor_cvm_code == REUSED_ROOT_PREDECESSORS[ticker]
    assert result.proof.successor_cvm_code == REUSED_ROOT_SUCCESSORS[ticker]
    assert result.proof.security_isin == _ISINS[ticker]
    assert result.proof.predecessor_last_session == _LAST_SESSIONS[ticker]
    assert result.proof.successor_first_session == _BOUNDARIES[ticker]
    assert set(result.proof.as_mapping()["sources"]) == {
        "cvm_fca.registrant",
        "cvm_fca.security",
        "b3.listed_supplement.successor",
        "b3.cotahist.identity",
    }


async def test_missing_predecessor_cnpj_stays_unprovable() -> None:
    recovery = B3ReusedRootRecovery(
        ticker_to_code={"JBSS3": REUSED_ROOT_PREDECESSORS["JBSS3"]},
        ticker_to_cnpj={},
        tape=_Tape({}, {}),
    )

    result = await recovery.prove("JBSS3", _company("JBSS3"))

    assert result.proof is None
    assert result.reason == "predecessor-cnpj-missing"


async def test_legacy_tape_code_can_witness_an_older_predecessor_event() -> None:
    ticker = "MOAR3"
    latest = B3TapeObservation(
        session=_LAST_SESSIONS[ticker],
        isin=_ISINS[ticker],
        especi="ON",
        bdi="02",
        name="MONT ARANHA",
        code="MOAR3",
    )
    legacy = B3TapeObservation(
        session=date(1995, 11, 28),
        isin=_ISINS[ticker],
        especi="ON *",
        bdi="02",
        name="MONT ARANHA",
        code="MOA 3",
    )
    recovery = B3ReusedRootRecovery(
        ticker_to_code={ticker: REUSED_ROOT_PREDECESSORS[ticker]},
        ticker_to_cnpj={ticker: _CNPJS[ticker]},
        tape=_Tape(
            latest={ticker: latest},
            sessions={(ticker, latest.session): latest},
            legacy=legacy,
        ),
    )
    result = await recovery.prove(ticker, _company(ticker))
    assert result.proof is not None

    decision = await recovery.capital_event(
        result.proof,
        {
            "isinCode": _ISINS[ticker],
            "lastDatePrior": "28/11/1995",
        },
    )

    assert decision.accepted
    assert decision.evidence["tape_code"] == "MOA 3"


class _CapitalTransport(httpx.AsyncBaseTransport):
    def __init__(self, body: dict[str, object]) -> None:
        self.body = body

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        if "GetDetail" in request.url.path:
            return httpx.Response(200, json={})
        return httpx.Response(200, json=self.body)


async def test_capital_rows_keep_the_predecessor_and_exclude_the_successor() -> None:
    ticker = "JBSS3"
    body = {
        "code": "JBSS",
        "codeCVM": REUSED_ROOT_SUCCESSORS[ticker],
        "tradingName": "JBS N.V.",
        "quotedPerSharSince": "09/06/2025",
        "stockDividends": [
            {
                "assetIssued": "BRJ8BSARNPR0",
                "factor": "50,00000000000",
                "approvedOn": "23/05/2025",
                "isinCode": _ISINS[ticker],
                "label": "INCORPORACAO",
                "lastDatePrior": "06/06/2025",
                "remarks": "",
            },
            {
                "assetIssued": "BRJBSSBDR002",
                "factor": "1,00000000000",
                "approvedOn": "01/07/2026",
                "isinCode": "BRJBSSBDR002",
                "label": "DIVIDENDO",
                "lastDatePrior": "18/05/2026",
                "remarks": "",
            },
        ],
    }
    reporter = _Reporter()
    recovery = _recovery(ticker)
    async with httpx.AsyncClient(transport=_CapitalTransport(body)) as http:
        source = B3CapitalEventSource(
            http,
            ticker_to_code={ticker: REUSED_ROOT_PREDECESSORS[ticker]},
            base_url="https://b3.test",
            validation_reporter=reporter,
            reused_root_recovery=recovery,
        )
        results = await source.fetch(ticker, CAPITAL_EVENT_B3_MODULE)

    assert len(results) == 1
    assert results[0].source == "b3"
    assert results[0].cvm_code == REUSED_ROOT_PREDECESSORS[ticker]
    assert results[0].payload["isin_code"] == _ISINS[ticker]
    identity = results[0].request["historical_identity"]
    assert identity["predecessor"]["cvm_code"] == "20575"
    excluded = reporter.reports[-1].evidence["excluded_rows"]
    assert excluded[0]["reason"] == "successor-event-excluded"


class _CashTransport(httpx.AsyncBaseTransport):
    def __init__(
        self, supplement: dict[str, object], rows: list[dict[str, object]]
    ) -> None:
        self.supplement = supplement
        self.rows = rows

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        if "GetDetail" in request.url.path:
            return httpx.Response(200, json={})
        if "GetListedSupplementCompany" in request.url.path:
            return httpx.Response(200, json=self.supplement)
        encoded = request.url.path.rsplit("/", 1)[-1]
        params = json.loads(base64.b64decode(encoded).decode())
        page = int(str(params["pageNumber"]))
        return httpx.Response(
            200,
            json={
                "page": {"pageNumber": page, "totalPages": 1},
                "results": self.rows,
            },
        )


async def test_cash_rows_keep_the_old_tape_and_persist_the_identity_chain() -> None:
    ticker = "PETZ3"
    supplement = {
        "code": "PETZ",
        "codeCVM": REUSED_ROOT_SUCCESSORS[ticker],
        "tradingName": "PETZ",
        "quotedPerSharSince": "21/05/2026",
    }
    old_row = {
        "typeStock": "ON",
        "dateApproval": "29/08/2024",
        "valueCash": "0,28829724720",
        "corporateAction": "DIVIDENDO",
        "lastDatePriorEx": "13/11/2024",
        "closingPricePriorExDate": "10,00",
        "quotedPerShares": "1",
        "corporateActionPrice": "2,88",
    }
    successor_row = {**old_row, "lastDatePriorEx": "01/06/2026"}
    reporter = _Reporter()
    async with httpx.AsyncClient(
        transport=_CashTransport(supplement, [old_row, successor_row])
    ) as http:
        source = B3CashDividendSource(
            http,
            ticker_to_code={ticker: REUSED_ROOT_PREDECESSORS[ticker]},
            base_url="https://b3.test",
            validation_reporter=reporter,
            reused_root_recovery=_recovery(ticker),
        )
        results = await source.fetch(ticker, CASH_DIVIDEND_B3_MODULE)

    assert len(results) == 1
    assert results[0].source == "b3"
    assert results[0].cvm_code == REUSED_ROOT_PREDECESSORS[ticker]
    identity = results[0].payload["historical_identity"]
    assert identity["security"]["isin"] == _ISINS[ticker]
    excluded = reporter.reports[-1].evidence["excluded_rows"]
    assert excluded[0]["reason"] == "successor-event-excluded"


async def test_recovery_does_not_turn_unproven_rows_into_an_empty_history() -> None:
    ticker = "MOAR3"
    body = {
        "code": "MOAR",
        "codeCVM": REUSED_ROOT_SUCCESSORS[ticker],
        "tradingName": "MONT ARANHA",
        "quotedPerSharSince": "15/05/2026",
        "stockDividends": [
            {
                "assetIssued": "",
                "factor": "100,00000000000",
                "approvedOn": "12/02/2026",
                "isinCode": _ISINS[ticker],
                "label": "RESG TOTAL RV",
                "lastDatePrior": "19/02/2026",
                "remarks": "",
            }
        ],
    }
    async with httpx.AsyncClient(transport=_CapitalTransport(body)) as http:
        source = B3CapitalEventSource(
            http,
            ticker_to_code={ticker: REUSED_ROOT_PREDECESSORS[ticker]},
            base_url="https://b3.test",
            reused_root_recovery=_recovery(ticker),
        )
        with pytest.raises(SourceBatchValidationError, match="coverage-established"):
            await source.fetch(ticker, CAPITAL_EVENT_B3_MODULE)
