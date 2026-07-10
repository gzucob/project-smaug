"""Source-selection seam: config picks brapi or CVM, same port (no network)."""

import httpx

from smaug.entrypoints.cli import _build_data_source
from smaug.ingestion.infrastructure.brapi_client import BrapiClient
from smaug.ingestion.infrastructure.cvm_capital import CvmCapitalSource
from smaug.ingestion.infrastructure.cvm_source import CvmDataSource
from smaug.ingestion.infrastructure.routed_source import RoutedDataSource
from smaug.shared.config import DEFAULT_BRAPI_MODULES, DEFAULT_CVM_MODULES, Settings


def test_active_modules_follows_selected_source() -> None:
    assert Settings(ingestion_source="cvm").active_modules == DEFAULT_CVM_MODULES
    assert Settings(ingestion_source="brapi").active_modules == DEFAULT_BRAPI_MODULES


async def test_build_data_source_selects_implementation_by_config() -> None:
    async with httpx.AsyncClient() as http:
        cvm = _build_data_source(Settings(ingestion_source="cvm"), http)
        brapi = _build_data_source(
            Settings(ingestion_source="brapi", brapi_token="tok"), http
        )

    # CVM needs two archives — statements and share counts — behind one router.
    assert isinstance(cvm, RoutedDataSource)
    assert isinstance(cvm._default, CvmDataSource)
    assert isinstance(cvm._routes["CAPITAL"], CvmCapitalSource)
    assert isinstance(brapi, BrapiClient)
