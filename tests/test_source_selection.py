"""Source-selection seam: config picks brapi or CVM, same port (no network)."""

import httpx

from smaug.analysis.infrastructure.b3_prices import CotahistArchive
from smaug.entrypoints.cli import _build_archive, _build_data_source
from smaug.ingestion.infrastructure.brapi_client import BrapiClient
from smaug.ingestion.infrastructure.cvm_capital import CvmCapitalSource
from smaug.ingestion.infrastructure.cvm_source import CvmDataSource
from smaug.ingestion.infrastructure.routed_source import RoutedDataSource
from smaug.shared.config import DEFAULT_BRAPI_MODULES, DEFAULT_CVM_MODULES, Settings


def test_active_modules_follows_selected_source() -> None:
    assert Settings(ingestion_source="cvm").active_modules == DEFAULT_CVM_MODULES
    assert Settings(ingestion_source="brapi").active_modules == DEFAULT_BRAPI_MODULES


def test_only_the_source_that_makes_a_request_per_call_is_paced() -> None:
    # CVM serves every call from one downloaded archive, so pacing them buys
    # nothing and costs a whole-exchange run two hundred hours.
    paced = Settings(ingestion_source="brapi", request_delay_seconds=2.0)
    assert paced.active_delay_seconds == 2.0
    assert Settings(ingestion_source="cvm").active_delay_seconds == 0.0


async def test_build_data_source_selects_implementation_by_config() -> None:
    # The CVM key maps are resolved upstream (curated nine + FCA registry) and
    # passed in; brapi ignores them (it keys off the ticker directly).
    code = {"PETR4": "9512"}
    cnpj = {"PETR4": "33.000.167/0001-01"}
    async with httpx.AsyncClient() as http:
        cvm = _build_data_source(Settings(ingestion_source="cvm"), http, code, cnpj)
        brapi = _build_data_source(
            Settings(ingestion_source="brapi", brapi_token="tok"), http, code, cnpj
        )

    # CVM needs two archives — statements and share counts — behind one router.
    assert isinstance(cvm, RoutedDataSource)
    assert isinstance(cvm._default, CvmDataSource)
    assert isinstance(cvm._routes["CAPITAL"], CvmCapitalSource)
    assert isinstance(brapi, BrapiClient)


async def test_the_exchange_prices_the_analysis_by_default() -> None:
    # ADR 0040. The vendor chain answers only when it is asked for by name, and
    # it is never a fallback: a company B3 does not list reads as a missing
    # price rather than silently from a source on another basis.
    async with httpx.AsyncClient() as http:
        assert isinstance(_build_archive(Settings(), http), CotahistArchive)
        assert _build_archive(Settings(price_source="vendors"), http) is None
