"""CvmDataSource: statement CSV parsing, the store-everything mirror, download."""

import zipfile
from pathlib import Path

import httpx
import pytest

from smaug.ingestion.infrastructure.cvm_source import (
    _ENCODING,
    CvmDataSource,
    _classify,
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

# The DMPL carries one extra column, and it is the one that tells its rows apart.
_DMPL_HEADER = (
    "CNPJ_CIA;DT_REFER;VERSAO;DENOM_CIA;CD_CVM;GRUPO_DFP;MOEDA;ESCALA_MOEDA;"
    "ORDEM_EXERC;DT_INI_EXERC;DT_FIM_EXERC;COLUNA_DF;CD_CONTA;DS_CONTA;VL_CONTA;"
    "ST_CONTA_FIXA"
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


def _dmpl_row(cd_cvm: str, ref: str, conta: str, coluna: str, valor: str) -> str:
    return (
        f"00.0/0001-00;{ref};1;ACME S.A.;{cd_cvm};DF Consolidado;REAL;MIL;"
        f"ÚLTIMO;{ref[:4]}-01-01;{ref};{coluna};{conta};Conta {conta};{valor};S"
    )


def _statement_zip(
    path: Path, members: dict[str, list[str]], *, header: str = _HEADER
) -> None:
    """A CVM statement ZIP, latin-1 encoded like the real open-data files."""
    with zipfile.ZipFile(path, "w") as z:
        for name, rows in members.items():
            body = "\n".join([header, *rows]) + "\n"
            z.writestr(name, body.encode(_ENCODING))


def _source(tmp_path: Path, mapping: dict[str, str]) -> CvmDataSource:
    return CvmDataSource(
        httpx.AsyncClient(), mapping, year=2021, cache_dir=str(tmp_path), document="DFP"
    )


def test_every_portfolio_ticker_has_a_cvm_code() -> None:
    # A missing code would make that ticker silently skip during collection.
    assert set(portfolio_tickers()) == set(TICKER_TO_CVM_CODE)
    assert all(code.strip() for code in TICKER_TO_CVM_CODE.values())


def test_classify_identifies_every_statement_member() -> None:
    assert _classify("dfp_cia_aberta_BPA_con_2021.csv") == ("BPA", "consolidated")
    assert _classify("itr_cia_aberta_BPP_ind_2024.csv") == ("BPP", "individual")
    assert _classify("dfp_cia_aberta_DFC_MI_con_2021.csv") == ("DFC", "consolidated")
    assert _classify("dfp_cia_aberta_DFC_MD_ind_2021.csv") == ("DFC", "individual")
    # Mirrored since ADR 0016 — the mirror does not decide what will be useful.
    assert _classify("dfp_cia_aberta_DVA_con_2021.csv") == ("DVA", "consolidated")
    assert _classify("dfp_cia_aberta_DMPL_ind_2021.csv") == ("DMPL", "individual")
    assert _classify("dfp_cia_aberta_DRA_con_2021.csv") == ("DRA", "consolidated")
    # The non-statement members carry no con/ind split and are skipped.
    assert _classify("dfp_cia_aberta_2021.csv") is None
    assert _classify("dfp_cia_aberta_composicao_capital_2021.csv") is None


def _stmt(**overrides: object) -> _Statement:
    defaults: dict[str, object] = {
        "module": "BPA",
        "reference_date": "2021-12-31",
        "version": 1,
        "balance_type": "consolidated",
        "ordem_exerc": "ULTIMO",
        "company_name": "WEG S.A.",
        "currency": "BRL",
        "currency_size": 1000,
        "period_start": "2021-01-01",
        "period_end": "2021-12-31",
        "accounts": [{"code": "1", "name": "Ativo Total", "quantity": "100.5"}],
    }
    return _Statement(**{**defaults, **overrides})  # type: ignore[arg-type]


def test_to_payload_mirrors_raw_accounts_without_math(tmp_path: Path) -> None:
    payload = _source(tmp_path, {"WEGE3": "5410"})._to_payload("5410", _stmt())

    assert payload["cvm_code"] == "5410"
    assert payload["document_type"] == "DFP"
    assert payload["reference_date"] == "2021-12-31"
    assert payload["balance_type"] == "consolidated"
    assert payload["currency_size"] == 1000
    assert payload["accounts"][0]["quantity"] == "100.5"  # exact, untouched
    # The discriminators the reader needs to make the selection the mirror no
    # longer makes for it (ADR 0016).
    assert payload["version"] == 1
    assert payload["ordem_exerc"] == "ULTIMO"


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
    quarters = ["2025-03-31", "2025-06-30", "2025-09-30"]
    async with httpx.AsyncClient() as http:
        source = CvmDataSource(
            http, {"PETR4": "9512"}, year=2025, cache_dir=str(tmp_path)
        )
        source._index = {
            "9512": [_stmt(reference_date=ref, period_end=ref) for ref in quarters]
        }
        results = await source.fetch("PETR4", "BPA")

    assert [r.payload["reference_date"] for r in results] == quarters
    assert all(
        r.request["reference_date"] == r.payload["reference_date"] for r in results
    )


def test_build_index_keeps_every_version_and_both_balance_types(
    tmp_path: Path,
) -> None:
    # ADR 0016: the mirror no longer picks the amendment or prefers the
    # consolidated statement. All three filings are kept — the reader chooses.
    zpath = tmp_path / "dfp_cia_aberta_2021.zip"
    _statement_zip(
        zpath,
        {
            "dfp_cia_aberta_BPA_con_2021.csv": [
                _row("005410", "2021-12-31", "1", "1", "100"),  # the original
                _row("005410", "2021-12-31", "2", "1", "200"),  # the amendment
            ],
            "dfp_cia_aberta_BPA_ind_2021.csv": [
                _row("005410", "2021-12-31", "2", "1", "999"),  # parent-only
            ],
        },
    )

    index = _source(tmp_path, {"WEGE3": "5410"})._build_index(zpath)

    filed = {
        (s.version, s.balance_type): s.accounts[0]["quantity"] for s in index["5410"]
    }
    assert filed == {
        (1, "consolidated"): "100",
        (2, "consolidated"): "200",
        (2, "individual"): "999",
    }


def test_build_index_keeps_the_individual_statement_of_a_filer_with_no_consolidated(
    tmp_path: Path,
) -> None:
    # SAPR11 files only individual statements — nothing to fall back *from*.
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

    (bpa,) = index["9493"]
    assert bpa.balance_type == "individual"
    assert bpa.accounts[0]["quantity"] == "500"


def test_build_index_folds_dfc_methods_and_keeps_the_comparative_period(
    tmp_path: Path,
) -> None:
    # The comparative (PENÚLTIMO) describes the *prior* period and is kept as a
    # statement of its own (ADR 0016) — it used to be dropped at ingestion.
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

    filed = {s.ordem_exerc: s.accounts[0]["quantity"] for s in index["5410"]}
    assert all(s.module == "DFC" for s in index["5410"])  # DFC_MI folded to DFC
    assert filed == {"ULTIMO": "42", "PENULTIMO": "40"}


def test_build_index_keeps_the_dmpl_column_that_tells_its_rows_apart(
    tmp_path: Path,
) -> None:
    # The DMPL is a matrix: these two rows share a CD_CONTA and differ only by
    # COLUNA_DF. Dropping that column would silently collapse them into one.
    zpath = tmp_path / "dfp_cia_aberta_2021.zip"
    _statement_zip(
        zpath,
        {
            "dfp_cia_aberta_DMPL_con_2021.csv": [
                _dmpl_row("005410", "2021-12-31", "5.05.01", "Patrimônio Líquido", "7"),
                _dmpl_row(
                    "005410",
                    "2021-12-31",
                    "5.05.01",
                    "Participação dos Não Controladores",
                    "3",
                ),
            ],
        },
        header=_DMPL_HEADER,
    )

    index = _source(tmp_path, {"WEGE3": "5410"})._build_index(zpath)

    (dmpl,) = index["5410"]
    assert dmpl.module == "DMPL"
    assert {a["column"]: a["quantity"] for a in dmpl.accounts} == {
        "Patrimônio Líquido": "7",
        "Participação dos Não Controladores": "3",
    }


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
