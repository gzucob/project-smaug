"""CvmDataSource: statement CSV parsing, version/consolidated selection, download."""

import zipfile
from pathlib import Path

import httpx
import pytest

from smaug.ingestion.infrastructure.cvm_source import (
    _ENCODING,
    CvmDataSource,
    _classify,
    _Document,
    _Statement,
)
from smaug.portfolio.domain.cvm_codes import TICKER_TO_CVM_CODE
from smaug.portfolio.domain.sectors import portfolio_tickers
from smaug.shared.errors import BrapiNotFoundError, CvmDownloadError
from tests.fakes import no_sleep

_HEADER = (
    "CNPJ_CIA;DT_REFER;VERSAO;DENOM_CIA;CD_CVM;GRUPO_DFP;MOEDA;ESCALA_MOEDA;"
    "ORDEM_EXERC;DT_INI_EXERC;DT_FIM_EXERC;CD_CONTA;DS_CONTA;VL_CONTA;ST_CONTA_FIXA"
)


def _row(
    cd_cvm: str,
    ref: str,
    version: str,
    conta: str,
    valor: str,
    *,
    ordem: str = "ÚLTIMO",
) -> str:
    return (
        f"00.0/0001-00;{ref};{version};ACME S.A.;{cd_cvm};DF Consolidado;REAL;MIL;"
        f"{ordem};{ref[:4]}-01-01;{ref};{conta};Conta {conta};{valor};S"
    )


def _statement_zip(path: Path, members: dict[str, list[str]]) -> None:
    """A CVM statement ZIP, latin-1 encoded like the real open-data files."""
    with zipfile.ZipFile(path, "w") as z:
        for name, rows in members.items():
            body = "\n".join([_HEADER, *rows]) + "\n"
            z.writestr(name, body.encode(_ENCODING))


def _source(tmp_path: Path, mapping: dict[str, str]) -> CvmDataSource:
    return CvmDataSource(
        httpx.AsyncClient(), mapping, year=2021, cache_dir=str(tmp_path), document="DFP"
    )


def test_every_portfolio_ticker_has_a_cvm_code() -> None:
    # A missing code would make that ticker silently skip during collection.
    assert set(portfolio_tickers()) == set(TICKER_TO_CVM_CODE)
    assert all(code.strip() for code in TICKER_TO_CVM_CODE.values())


def test_classify_identifies_statement_members() -> None:
    assert _classify("dfp_cia_aberta_BPA_con_2021.csv") == ("BPA", "consolidated")
    assert _classify("itr_cia_aberta_BPP_ind_2024.csv") == ("BPP", "individual")
    assert _classify("dfp_cia_aberta_DFC_MI_con_2021.csv") == ("DFC", "consolidated")
    assert _classify("dfp_cia_aberta_DFC_MD_ind_2021.csv") == ("DFC", "individual")
    # Statements we don't mirror, and the non-statement members, are skipped.
    assert _classify("dfp_cia_aberta_DVA_con_2021.csv") is None
    assert _classify("dfp_cia_aberta_DMPL_ind_2021.csv") is None
    assert _classify("dfp_cia_aberta_2021.csv") is None


def test_to_payload_mirrors_raw_accounts_without_math(tmp_path: Path) -> None:
    statement = _Statement(
        balance_type="consolidated",
        company_name="WEG S.A.",
        currency="BRL",
        currency_size=1000,
        period_start="2021-01-01",
        period_end="2021-12-31",
        accounts=[{"code": "1", "name": "Ativo Total", "quantity": "100.5"}],
    )
    doc = _Document("5410", "2021-12-31", {"BPA": statement})

    payload = _source(tmp_path, {"WEGE3": "5410"})._to_payload(doc, "BPA", statement)

    assert payload["cvm_code"] == "5410"
    assert payload["document_type"] == "DFP"
    assert payload["reference_date"] == "2021-12-31"
    assert payload["balance_type"] == "consolidated"
    assert payload["currency_size"] == 1000
    assert payload["accounts"][0]["quantity"] == "100.5"  # exact, untouched


async def test_dfp_document_targets_the_annual_file_and_url(tmp_path: Path) -> None:
    async with httpx.AsyncClient() as http:
        itr = CvmDataSource(http, {"PETR4": "9512"}, year=2024, cache_dir=str(tmp_path))
        dfp = CvmDataSource(
            http, {"PETR4": "9512"}, year=2024, cache_dir=str(tmp_path), document="DFP"
        )

    assert itr._zip_name == "itr_cia_aberta_2024.zip"  # default stays ITR
    assert itr._base_url.endswith("DOC/ITR/DADOS")
    assert dfp._zip_name == "dfp_cia_aberta_2024.zip"
    assert dfp._base_url.endswith("DOC/DFP/DADOS")


async def test_fetch_skips_unmapped_ticker_without_touching_network(
    tmp_path: Path,
) -> None:
    async with httpx.AsyncClient() as http:
        source = CvmDataSource(
            http, {"BBAS3": "1023"}, year=2024, cache_dir=str(tmp_path)
        )
        source._index = {}  # pretend the file is already loaded (empty)
        with pytest.raises(BrapiNotFoundError):
            await source.fetch("PETR4", "BPA")  # PETR4 not in the injected map


async def test_fetch_returns_one_result_per_filed_quarter(tmp_path: Path) -> None:
    # The ITR file carries Q1/Q2/Q3 as separate periods; fetch must surface all
    # three (this is exactly what the TTM needs — the earlier code kept only Q3).
    def _doc(ref: str) -> _Document:
        stmt = _Statement(
            balance_type="consolidated",
            company_name="PETROBRAS",
            currency="BRL",
            currency_size=1000,
            period_start=f"{ref[:4]}-01-01",
            period_end=ref,
            accounts=[{"code": "1", "name": "Ativo", "quantity": "1"}],
        )
        return _Document("9512", ref, {"BPA": stmt})

    quarters = ["2025-03-31", "2025-06-30", "2025-09-30"]
    async with httpx.AsyncClient() as http:
        source = CvmDataSource(
            http, {"PETR4": "9512"}, year=2025, cache_dir=str(tmp_path)
        )
        source._index = {"9512": [_doc(ref) for ref in quarters]}
        results = await source.fetch("PETR4", "BPA")

    assert [r.payload["reference_date"] for r in results] == quarters
    assert all(
        r.request["reference_date"] == r.payload["reference_date"] for r in results
    )


def test_build_index_prefers_consolidated_and_the_latest_version(
    tmp_path: Path,
) -> None:
    zpath = tmp_path / "dfp_cia_aberta_2021.zip"
    _statement_zip(
        zpath,
        {
            "dfp_cia_aberta_BPA_con_2021.csv": [
                _row("005410", "2021-12-31", "1", "1", "100"),  # superseded
                _row("005410", "2021-12-31", "2", "1", "200"),  # amendment wins
            ],
            "dfp_cia_aberta_BPA_ind_2021.csv": [
                _row("005410", "2021-12-31", "2", "1", "999"),  # individual: ignored
            ],
        },
    )

    index = _source(tmp_path, {"WEGE3": "5410"})._build_index(zpath)

    bpa = index["5410"][0].statements["BPA"]
    assert bpa.balance_type == "consolidated"
    assert bpa.accounts[0]["quantity"] == "200"  # v2 consolidated, not v1, not ind


def test_build_index_falls_back_to_individual_when_no_consolidated(
    tmp_path: Path,
) -> None:
    # SAPR11 files only individual statements — the fallback is correct, not a bug.
    zpath = tmp_path / "dfp_cia_aberta_2021.zip"
    _statement_zip(
        zpath,
        {
            "dfp_cia_aberta_BPA_ind_2021.csv": [
                _row("009493", "2021-12-31", "1", "1", "500")
            ],
        },
    )

    index = _source(tmp_path, {"SAPR11": "9493"})._build_index(zpath)

    bpa = index["9493"][0].statements["BPA"]
    assert bpa.balance_type == "individual"
    assert bpa.accounts[0]["quantity"] == "500"


def test_build_index_folds_dfc_methods_and_drops_the_comparative_period(
    tmp_path: Path,
) -> None:
    zpath = tmp_path / "dfp_cia_aberta_2021.zip"
    _statement_zip(
        zpath,
        {
            "dfp_cia_aberta_DFC_MI_con_2021.csv": [
                _row("005410", "2021-12-31", "1", "6.01", "42"),  # indirect method
                _row("005410", "2021-12-31", "1", "6.01", "40", ordem="PENÚLTIMO"),
            ],
        },
    )

    index = _source(tmp_path, {"WEGE3": "5410"})._build_index(zpath)

    dfc = index["5410"][0].statements["DFC"]  # DFC_MI folded to the DFC module
    assert [a["quantity"] for a in dfc.accounts] == ["42"]  # comparative dropped


ZIP_BYTES = b"PK\x05\x06" + b"\x00" * 18  # smallest valid (empty) ZIP


class _FlakyTransport(httpx.AsyncBaseTransport):
    """Cuts the connection ``failures`` times, then serves the ZIP."""

    def __init__(self, failures: int, status_code: int = 200) -> None:
        self.failures = failures
        self.requests = 0
        self._status_code = status_code

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        self.requests += 1
        if self.requests <= self.failures:
            raise httpx.RemoteProtocolError(
                "peer closed connection without sending complete message body",
                request=request,
            )
        return httpx.Response(self._status_code, content=ZIP_BYTES)


async def _download_with(transport: httpx.AsyncBaseTransport, dst: Path) -> None:
    async with httpx.AsyncClient(transport=transport) as http:
        source = CvmDataSource(
            http,
            {"PETR4": "9512"},
            year=2021,
            cache_dir=str(dst.parent),
            document="DFP",
            sleep=no_sleep,
        )
        await source._download(dst)


async def test_download_retries_a_cut_connection_and_succeeds(tmp_path: Path) -> None:
    transport = _FlakyTransport(failures=1)
    dst = tmp_path / "dfp_cia_aberta_2021.zip"

    await _download_with(transport, dst)

    assert transport.requests == 2  # first attempt cut, second healed it
    assert dst.read_bytes() == ZIP_BYTES
    assert not (tmp_path / "dfp_cia_aberta_2021.part").exists()  # renamed away


async def test_download_gives_up_after_retries_without_a_partial_file(
    tmp_path: Path,
) -> None:
    transport = _FlakyTransport(failures=99)
    dst = tmp_path / "dfp_cia_aberta_2021.zip"

    with pytest.raises(CvmDownloadError, match="giving up"):
        await _download_with(transport, dst)

    assert transport.requests == 3  # 1 attempt + 2 retries
    assert not dst.exists()  # nothing truncated left to poison the cache


async def test_download_retries_5xx_but_fails_404_immediately(tmp_path: Path) -> None:
    dst = tmp_path / "dfp_cia_aberta_2021.zip"

    flaky_5xx = _FlakyTransport(failures=0, status_code=503)
    with pytest.raises(CvmDownloadError, match="HTTP 503"):
        await _download_with(flaky_5xx, dst)
    assert flaky_5xx.requests == 3  # 5xx is transient: exhausted the retries

    missing_year = _FlakyTransport(failures=0, status_code=404)
    with pytest.raises(CvmDownloadError, match="HTTP 404"):
        await _download_with(missing_year, dst)
    assert missing_year.requests == 1  # 4xx is permanent: no retry
    assert not dst.exists()
