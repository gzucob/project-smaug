"""Composition root: one raw source, one price source, no vendor seam (ADR 0041)."""

import httpx
import pytest

from smaug.analysis.infrastructure.b3_prices import CotahistArchive
from smaug.entrypoints import cli
from smaug.entrypoints.cli import (
    _build_archive,
    _build_data_source,
    _parser_identities,
    format_fca_snapshot,
)
from smaug.ingestion.infrastructure.cvm_capital import CvmCapitalSource
from smaug.ingestion.infrastructure.cvm_source import CvmDataSource
from smaug.ingestion.infrastructure.routed_source import RoutedDataSource
from smaug.portfolio.domain.provenance import FcaSnapshotProvenance
from smaug.shared.config import DEFAULT_CVM_FCA_YEAR, DEFAULT_CVM_MODULES, Settings


def test_the_configured_modules_are_the_cvm_ones() -> None:
    assert Settings().cvm_modules == DEFAULT_CVM_MODULES


def test_the_current_fca_snapshot_has_its_own_configured_year() -> None:
    settings = Settings(cvm_year=2024, cvm_fca_year=DEFAULT_CVM_FCA_YEAR)

    assert settings.cvm_year == 2024
    assert settings.cvm_fca_year == DEFAULT_CVM_FCA_YEAR


def test_cvm_fca_year_can_be_overridden_without_moving_cvm_year(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("CVM_YEAR", "2023")
    monkeypatch.setenv("CVM_FCA_YEAR", "2025")

    settings = Settings()

    assert settings.cvm_year == 2023
    assert settings.cvm_fca_year == 2025


def test_fca_snapshot_output_names_year_source_and_artifact() -> None:
    output = format_fca_snapshot(
        FcaSnapshotProvenance(
            year=2026,
            source="cvm_fca",
            source_url="https://example.test/fca_2026.zip",
            artifact_id="sha256:" + "a" * 64,
        )
    )

    assert "FCA snapshot year=2026" in output
    assert "source=cvm_fca" in output
    assert "artifact=sha256:" + "a" * 64 in output


async def test_identity_resolution_uses_fca_year_not_accounting_year(
    monkeypatch,
) -> None:
    seen: dict[str, object] = {}

    class FakeRegistry:
        def __init__(self, _http, *, year, cache_dir, artifact_store=None) -> None:
            seen["year"] = year
            seen["cache_dir"] = cache_dir
            seen["artifact_store"] = artifact_store

        async def resolve_all(self, tickers):
            return {}

        async def provenance(self):
            return FcaSnapshotProvenance(
                year=2026,
                source="cvm_fca",
                source_url="https://example.test/fca_2026.zip",
            )

    monkeypatch.setattr(cli, "CvmCompanyRegistry", FakeRegistry)
    settings = Settings(cvm_year=2024, cvm_fca_year=2026)
    provenance: list[FcaSnapshotProvenance] = []
    async with httpx.AsyncClient() as http:
        assert (
            await cli._registry_identities(
                settings,
                http,
                (),
                fca_provenance=provenance,
            )
            == {}
        )

    assert seen["year"] == 2026
    assert provenance[0].year == 2026


async def test_current_universe_reuses_the_injected_bronze_store(monkeypatch) -> None:
    seen: dict[str, object] = {}
    bronze = object()

    class FakeRegistry:
        def __init__(self, _http, *, year, cache_dir, artifact_store=None) -> None:
            seen["year"] = year
            seen["artifact_store"] = artifact_store

        async def companies(self):
            return ()

        async def resolve_all(self, tickers):
            return {}

        async def provenance(self):
            return FcaSnapshotProvenance(
                year=2026,
                source="cvm_fca",
                source_url="https://example.test/fca_2026.zip",
                artifact_id="sha256:" + "b" * 64,
            )

    monkeypatch.setattr(cli, "CvmCompanyRegistry", FakeRegistry)
    settings = Settings(cvm_year=2024, cvm_fca_year=2026)
    provenance: list[FcaSnapshotProvenance] = []
    async with httpx.AsyncClient() as http:
        await cli._universe_tickers(
            settings,
            http,
            fca_provenance=provenance,
            artifact_store=bronze,  # type: ignore[arg-type]
        )

    assert seen["year"] == 2026
    assert seen["artifact_store"] is bronze
    assert provenance[0].artifact_id == "sha256:" + "b" * 64


def test_every_configured_parser_has_a_stable_name_and_version() -> None:
    identities = _parser_identities(Settings().cvm_modules)

    assert {identity.name: identity.version for identity in identities} == {
        "cvm.statements.csv": 1,
        # Version 2 joins FRE's capital-by-class child rows for PNA/PNB (#72).
        "cvm.capital.csv": 2,
        "cvm.treasury.csv": 1,
        "cvm.capital-events.csv": 1,
        "b3.capital-events.json": 1,
        "b3.cash-dividends.json": 1,
    }


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
    assert cvm._routes["CAPITAL"].archive_name == "fre_cia_aberta_2024.zip"  # type: ignore[attr-defined]
    assert cvm._default.archive_name == "dfp_cia_aberta_2024.zip"  # type: ignore[attr-defined]
    assert await cvm.artifact_for("CASH_DIVIDEND_B3") is None


async def test_accounting_archive_year_does_not_follow_fca_snapshot_year() -> None:
    settings = Settings(cvm_year=2024, cvm_fca_year=2026)
    code = {"PETR4": "9512"}
    cnpj = {"PETR4": "33.000.167/0001-01"}

    async with httpx.AsyncClient() as http:
        cvm = _build_data_source(settings, http, code, cnpj, year=2023)

    assert cvm._default.archive_name == "dfp_cia_aberta_2023.zip"  # type: ignore[attr-defined]
    assert (
        cvm._routes["CAPITAL"].archive_name  # type: ignore[attr-defined]
        == "fre_cia_aberta_2023.zip"
    )


async def test_the_exchange_prices_the_analysis_and_nothing_else_does() -> None:
    # There is no second source to fall back to: a company B3 does not list
    # reads as a missing price rather than as somebody else's number on
    # another basis (ADR 0041).
    async with httpx.AsyncClient() as http:
        assert isinstance(_build_archive(Settings(), http), CotahistArchive)
