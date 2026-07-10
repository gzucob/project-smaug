"""CvmDataSource pure logic: DMPL sanitizer, payload shaping, download retry."""

import zipfile
from datetime import date
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest

from smaug.ingestion.infrastructure.cvm_source import (
    CvmDataSource,
    _consolidated_keys,
    _dropped_consolidated,
    _sanitize_dmpl,
)
from smaug.portfolio.domain.cvm_codes import TICKER_TO_CVM_CODE
from smaug.portfolio.domain.sectors import portfolio_tickers
from smaug.shared.errors import (
    BrapiNotFoundError,
    CvmConsolidatedDroppedError,
    CvmDownloadError,
)
from tests.fakes import no_sleep


def test_every_portfolio_ticker_has_a_cvm_code() -> None:
    # A missing code would make that ticker silently skip during collection.
    assert set(portfolio_tickers()) == set(TICKER_TO_CVM_CODE)
    assert all(code.strip() for code in TICKER_TO_CVM_CODE.values())


def test_sanitize_dmpl_keeps_header_only_and_leaves_others_intact(
    tmp_path: Path,
) -> None:
    src = tmp_path / "in.zip"
    with zipfile.ZipFile(src, "w") as z:
        z.writestr("itr_cia_aberta_DMPL_con_2024.csv", "H1;H2\nrow;a\nrow;b\n")
        z.writestr("itr_cia_aberta_BPA_con_2024.csv", "H1;H2\nkeep;me\n")

    dst = tmp_path / "out.zip"
    _sanitize_dmpl(src, dst)

    with zipfile.ZipFile(dst) as z:
        dmpl = z.read("itr_cia_aberta_DMPL_con_2024.csv").decode()
        bpa = z.read("itr_cia_aberta_BPA_con_2024.csv").decode()
    assert dmpl == "H1;H2\n"  # rows stripped, header kept
    assert bpa == "H1;H2\nkeep;me\n"  # untouched


def test_to_payload_mirrors_raw_accounts_without_math() -> None:
    accounts = [
        SimpleNamespace(
            code="1",
            name="Ativo Total",
            quantity=Decimal("100.5"),
            level=1,
            is_fixed=True,
        ),
    ]
    statement = SimpleNamespace(
        accounts=accounts,
        currency="BRL",
        currency_size=1000,
        period_end_date=date(2024, 9, 30),
    )
    doc = SimpleNamespace(
        cvm_code="1023",
        company_name="BCO BRASIL S.A.",
        type=SimpleNamespace(name="ITR"),
        reference_date=date(2024, 9, 30),
    )

    payload = CvmDataSource._to_payload(doc, "BPA", "consolidated", statement)

    assert payload["cvm_code"] == "1023"
    assert payload["statement"] == "BPA"
    assert payload["reference_date"] == "2024-09-30"
    assert payload["currency_size"] == 1000
    assert payload["accounts"][0]["quantity"] == "100.5"  # exact, as string
    assert payload["accounts"][0]["name"] == "Ativo Total"


async def test_dfp_document_targets_the_annual_file_and_url(tmp_path: Path) -> None:
    async with httpx.AsyncClient() as http:
        itr = CvmDataSource(http, {"PETR4": "9512"}, year=2024, cache_dir=str(tmp_path))
        dfp = CvmDataSource(
            http,
            {"PETR4": "9512"},
            year=2024,
            cache_dir=str(tmp_path),
            document="DFP",
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


def _fake_itr_doc(ref: date) -> SimpleNamespace:
    """A minimal pycvm-shaped ITR document carrying one BPA account."""
    statement = SimpleNamespace(
        accounts=[
            SimpleNamespace(
                code="1", name="Ativo Total", quantity=Decimal("1"), level=1
            )
        ],
        currency="BRL",
        currency_size=1000,
        period_start_date=date(ref.year, 1, 1),
        period_end_date=ref,
    )
    collection = SimpleNamespace(bpa=statement)
    return SimpleNamespace(
        cvm_code="9512",
        company_name="PETROLEO BRASILEIRO S.A. PETROBRAS",
        type=SimpleNamespace(name="ITR"),
        reference_date=ref,
        consolidated=SimpleNamespace(last=collection),
        individual=None,
    )


async def test_fetch_returns_one_result_per_filed_quarter(tmp_path: Path) -> None:
    # The ITR file carries Q1/Q2/Q3 as separate documents; fetch must surface all
    # three (this is exactly what the TTM needs — the earlier code kept only Q3).
    quarters = [date(2025, 3, 31), date(2025, 6, 30), date(2025, 9, 30)]
    async with httpx.AsyncClient() as http:
        source = CvmDataSource(
            http, {"PETR4": "9512"}, year=2025, cache_dir=str(tmp_path)
        )
        source._index = {"9512": [_fake_itr_doc(ref) for ref in quarters]}
        results = await source.fetch("PETR4", "BPA")

    assert [r.payload["reference_date"] for r in results] == [
        "2025-03-31",
        "2025-06-30",
        "2025-09-30",
    ]
    assert all(
        r.request["reference_date"] == r.payload["reference_date"] for r in results
    )


def _write_bpa_con(path: Path, rows: list[tuple[str, str, str]]) -> None:
    """A DFP ZIP with one BPA_con CSV of ``(CD_CVM, DT_REFER, VERSAO)`` rows."""
    header = "CNPJ_CIA;DT_REFER;VERSAO;CD_CVM;CD_CONTA;VL_CONTA"
    body = [f"00.0/0001-00;{ref};{ver};{cd};1;100" for cd, ref, ver in rows]
    with zipfile.ZipFile(path, "w") as z:
        z.writestr("dfp_cia_aberta_BPA_con_2021.csv", "\n".join([header, *body]) + "\n")


def _doc(
    code: str, ref: date, *, version: int, consolidated: object
) -> SimpleNamespace:
    return SimpleNamespace(
        cvm_code=code, reference_date=ref, version=version, consolidated=consolidated
    )


def test_consolidated_keys_reads_only_wanted_codes(tmp_path: Path) -> None:
    src = tmp_path / "dfp.zip"
    # Only 5410 is wanted; the padded 099999 row must be ignored.
    _write_bpa_con(src, [("005410", "2021-12-31", "1"), ("099999", "2021-12-31", "1")])

    keys = _consolidated_keys(src, wanted={"5410"})

    assert keys == {("5410", "2021-12-31", 1)}  # zero-padded CD_CVM folded to "5410"


def test_dropped_consolidated_spares_genuine_individual_filer() -> None:
    keys = {("5410", "2021-12-31", 1)}
    docs = [
        _doc("5410", date(2021, 12, 31), version=1, consolidated=None),  # #55 desync
        _doc("9493", date(2021, 12, 31), version=1, consolidated=None),  # no con filed
        _doc("5410", date(2022, 12, 31), version=1, consolidated=object()),  # present
    ]

    # Only the doc whose consolidated is filed yet missing is flagged.
    assert _dropped_consolidated(docs, keys) == [("5410", "2021-12-31")]


async def test_guard_aborts_when_pycvm_drops_a_filed_consolidated(
    tmp_path: Path,
) -> None:
    src = tmp_path / "dfp_cia_aberta_2021.zip"
    _write_bpa_con(src, [("005410", "2021-12-31", "1")])
    dropped = _doc("5410", date(2021, 12, 31), version=1, consolidated=None)

    async with httpx.AsyncClient() as http:
        source = CvmDataSource(
            http, {"WEGE3": "5410"}, year=2021, cache_dir=str(tmp_path), document="DFP"
        )
        index = {"5410": [dropped]}
        with pytest.raises(CvmConsolidatedDroppedError, match="WEGE3 2021-12-31"):
            source._raise_if_consolidated_dropped(src, {"5410"}, index)


async def test_guard_allows_a_genuine_individual_only_filer(tmp_path: Path) -> None:
    # SAPR11 files no consolidated statement, so the raw file has no BPA_con row
    # for it — mirroring its individual statement is correct, not the #55 bug.
    src = tmp_path / "dfp_cia_aberta_2021.zip"
    _write_bpa_con(src, [])  # header only
    only = _doc("9493", date(2021, 12, 31), version=1, consolidated=None)

    async with httpx.AsyncClient() as http:
        source = CvmDataSource(
            http, {"SAPR11": "9493"}, year=2021, cache_dir=str(tmp_path), document="DFP"
        )
        source._raise_if_consolidated_dropped(src, {"9493"}, {"9493": [only]})


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
