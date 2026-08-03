"""Composition root: one raw source, one price source, no vendor seam (ADR 0041)."""

import httpx

from smaug.analysis.infrastructure.b3_prices import CotahistArchive
from smaug.entrypoints.cli import _build_archive, _build_data_source
from smaug.ingestion.infrastructure.cvm_capital import CvmCapitalSource
from smaug.ingestion.infrastructure.cvm_source import CvmDataSource
from smaug.ingestion.infrastructure.routed_source import RoutedDataSource
from smaug.shared.config import DEFAULT_CVM_MODULES, Settings


def test_the_configured_modules_are_the_cvm_ones() -> None:
    assert Settings().cvm_modules == DEFAULT_CVM_MODULES


async def test_build_data_source_routes_each_module_to_its_own_archive() -> None:
    # The key maps are resolved upstream (curated nine + FCA registry) and
    # passed in.
    code = {"PETR4": "9512"}
    cnpj = {"PETR4": "33.000.167/0001-01"}
    async with httpx.AsyncClient() as http:
        cvm = _build_data_source(Settings(), http, code, cnpj)

    # Two archives — statements and share counts — behind one router, plus the
    # exchange's own endpoints, which have no archive at all.
    assert isinstance(cvm, RoutedDataSource)
    assert isinstance(cvm._default, CvmDataSource)
    assert isinstance(cvm._routes["CAPITAL"], CvmCapitalSource)
    assert cvm.archive_for("CAPITAL") == "fre_cia_aberta_2024.zip"
    assert cvm.archive_for("DRE") == "dfp_cia_aberta_2024.zip"
    assert cvm.archive_for("CASH_DIVIDEND_B3") is None


async def test_the_exchange_prices_the_analysis_and_nothing_else_does() -> None:
    # There is no second source to fall back to: a company B3 does not list
    # reads as a missing price rather than as somebody else's number on
    # another basis (ADR 0041).
    async with httpx.AsyncClient() as http:
        assert isinstance(_build_archive(Settings(), http), CotahistArchive)
