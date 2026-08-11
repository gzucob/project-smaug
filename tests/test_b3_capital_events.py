"""B3CapitalEventSource: the exchange's corporate-action history, mirrored as filed.

The payloads are real ``GetListedSupplementCompany`` rows — BBAS3's 2024 split
(listed three times, once per ISIN) and VIVT3's 2025 composite action.
"""

from __future__ import annotations

import json

import httpx
import pytest

from smaug.ingestion.domain.validation import SourceBatchValidation
from smaug.ingestion.infrastructure.b3_capital_events import (
    CAPITAL_EVENT_B3_MODULE,
    B3CapitalEventSource,
)
from smaug.shared.errors import SourceBatchValidationError, SourceNotFoundError

BBAS_ROWS = [
    {
        "assetIssued": "BRBBASACNOR3",
        "factor": "100,00000000000",
        "approvedOn": "02/02/2024",
        "isinCode": "BRBBASACNOR3",
        "label": "DESDOBRAMENTO",
        "lastDatePrior": "15/04/2024",
        "remarks": "",
    },
    {
        "assetIssued": "BRBBASA04OR8",
        "factor": "100,00000000000",
        "approvedOn": "02/02/2024",
        "isinCode": "BRBBASA04OR8",
        "label": "DESDOBRAMENTO",
        "lastDatePrior": "15/04/2024",
        "remarks": "",
    },
    {
        "assetIssued": "BRBBASACNOR3",
        "factor": "0,00100000000",
        "approvedOn": "12/11/2003",
        "isinCode": "BRBBASACNOR3",
        "label": "GRUPAMENTO",
        "lastDatePrior": "23/01/2004",
        "remarks": "",
    },
]


class _Transport(httpx.AsyncBaseTransport):
    """Serves one body and records the paths it was asked for."""

    def __init__(self, body: object, status: int = 200) -> None:
        self.paths: list[str] = []
        self._body = body
        self._status = status

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        self.paths.append(request.url.path)
        if self._body is None:
            return httpx.Response(self._status, content=b"")
        return httpx.Response(self._status, content=json.dumps(self._body).encode())


def _source(
    body: object, *, code: str | None = "1023"
) -> tuple[B3CapitalEventSource, httpx.AsyncClient, _Transport]:
    transport = _Transport(body)
    http = httpx.AsyncClient(transport=transport)
    codes = {"BBAS3": code} if code else {}
    return (
        B3CapitalEventSource(http, ticker_to_code=codes, base_url="https://b3.test"),
        http,
        transport,
    )


async def test_every_filed_row_is_mirrored_as_b3_publishes_it() -> None:
    source, http, _ = _source({"codeCVM": "1023", "stockDividends": BBAS_ROWS})

    async with http:
        results = await source.fetch("BBAS3", CAPITAL_EVENT_B3_MODULE)

    assert len(results) == 3  # including the two ISINs of one event
    assert {result.source for result in results} == {"b3"}
    split = results[0].payload
    # The factor stays a pt-BR string and the label stays B3's own word: a
    # DESDOBRAMENTO of 100 is a 2:1 split, and knowing that is Phase 2's job.
    assert split["factor"] == "100,00000000000"
    assert split["event_type"] == "DESDOBRAMENTO"
    assert split["approval_date"] == "02/02/2024"
    # The last session quoted on the old base — the one thing CVM never files.
    assert split["last_date_prior"] == "15/04/2024"
    assert split["isin_code"] == "BRBBASACNOR3"


async def test_the_row_is_keyed_on_the_registrant_the_mirror_reads_by() -> None:
    # ADR 0030: a filing is the company's, not the ticker's. A row stored without
    # the CD_CVM is invisible to every reader that resolves one.
    source, http, _ = _source({"codeCVM": "1023", "stockDividends": BBAS_ROWS})

    async with http:
        results = await source.fetch("BBAS3", CAPITAL_EVENT_B3_MODULE)

    assert {result.cvm_code for result in results} == {"1023"}


async def test_b3s_own_code_stands_in_when_the_registry_has_none() -> None:
    source, http, _ = _source(
        {"codeCVM": "1023", "stockDividends": BBAS_ROWS}, code=None
    )

    async with http:
        results = await source.fetch("BBAS3", CAPITAL_EVENT_B3_MODULE)

    assert results[0].cvm_code == "1023"


async def test_the_company_is_addressed_by_its_trading_root() -> None:
    # B3 keys this endpoint on the four-character root, not on the ticker and not
    # on the CVM code. TAEE11 and TAEE4 are one company here.
    source, http, transport = _source({"stockDividends": BBAS_ROWS})

    async with http:
        await source.fetch("BBAS3", CAPITAL_EVENT_B3_MODULE)

    assert "BBAS" in _decoded(transport.paths[0])
    assert "BBAS3" not in _decoded(transport.paths[0])


async def test_a_company_with_no_corporate_action_is_an_absence_not_a_blank() -> None:
    # Most companies have never split. The mirror records that the source was
    # asked and had nothing, rather than storing an empty list as a finding.
    source, http, _ = _source({"codeCVM": "1023", "stockDividends": []})

    async with http:
        with pytest.raises(SourceNotFoundError):
            await source.fetch("BBAS3", CAPITAL_EVENT_B3_MODULE)


async def test_an_empty_body_quarantines_unestablished_coverage() -> None:
    source, http, _ = _source(None)

    async with http:
        with pytest.raises(SourceBatchValidationError, match="coverage-established"):
            await source.fetch("BBAS3", CAPITAL_EVENT_B3_MODULE)


async def test_invalid_b3_output_is_retained_in_the_validation_report() -> None:
    class _Reporter:
        def __init__(self) -> None:
            self.reports: list[SourceBatchValidation] = []

        async def record(self, validation: SourceBatchValidation) -> None:
            self.reports.append(validation)

    reporter = _Reporter()
    source, http, _ = _source({"codeCVM": "1023"})
    source._validation_reporter = reporter

    async with http:
        with pytest.raises(SourceBatchValidationError, match="response-schema"):
            await source.fetch("BBAS3", CAPITAL_EVENT_B3_MODULE)

    report = reporter.reports[0]
    assert report.status.value == "quarantined"
    assert report.evidence == {"codeCVM": "1023"}


async def test_the_endpoint_answering_with_a_list_is_unwrapped() -> None:
    # It returns a bare object for some companies and a one-element array for
    # others, with the same content.
    source, http, _ = _source([{"codeCVM": "1023", "stockDividends": BBAS_ROWS}])

    async with http:
        results = await source.fetch("BBAS3", CAPITAL_EVENT_B3_MODULE)

    assert len(results) == 3


def _decoded(path: str) -> str:
    import base64

    return base64.b64decode(path.rsplit("/", 1)[-1]).decode()
