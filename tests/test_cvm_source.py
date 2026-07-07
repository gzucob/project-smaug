"""CvmDataSource pure logic: DMPL sanitizer, payload shaping, skip on unmapped."""

import zipfile
from datetime import date
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest

from smaug.ingestion.infrastructure.cvm_source import CvmDataSource, _sanitize_dmpl
from smaug.portfolio.domain.cvm_codes import TICKER_TO_CVM_CODE
from smaug.portfolio.domain.sectors import portfolio_tickers
from smaug.shared.errors import BrapiNotFoundError


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
