"""The installed backend explains public formulas without source checkout or DBs."""

import json
import re
from pathlib import Path

from httpx import ASGITransport, AsyncClient
from typer.testing import CliRunner

from smaug.analysis.domain.indicators import (
    CALCULATION_CONTRACT_VERSION,
    indicator_contracts,
    public_indicator_names,
)
from smaug.analysis.infrastructure.formulas import formula_references
from smaug.entrypoints.api import app
from smaug.entrypoints.cli import app as cli


def test_catalog_covers_public_indicators_and_existing_calculation_contracts() -> None:
    references = formula_references()
    assert {item.indicator for item in references} == set(public_indicator_names())
    contracts = indicator_contracts(CALCULATION_CONTRACT_VERSION)
    for item in references:
        assert item.formula
        assert item.meaning
        assert item.unit
        assert item.period
        assert item.contract == contracts.get(item.indicator)


def test_editorial_formulas_and_caveats_match_backend_reference() -> None:
    source = (
        Path(__file__).parents[1] / "frontend/src/lib/indicator-docs.ts"
    ).read_text()
    by_name = {item.indicator: item for item in formula_references()}
    for name, block in re.findall(r"^  (\w+): \{(.*?)^  \},", source, re.M | re.S):
        for field, expected in (
            ("formula", by_name[name].formula),
            ("what", by_name[name].meaning),
            ("caveat", by_name[name].caveat),
        ):
            match = re.search(r"\b" + field + r':\s*("(?:[^"\\]|\\.)*")', block)
            assert (json.loads(match[1]) if match else None) == expected, name


def test_cli_lists_and_explains_without_database_connections() -> None:
    runner = CliRunner()
    result = runner.invoke(cli, ["formulas", "--json"])
    assert result.exit_code == 0
    assert len(json.loads(result.output)) == len(public_indicator_names())
    detail = runner.invoke(cli, ["formulas", "pe_basic", "--json"])
    assert detail.exit_code == 0
    assert json.loads(detail.output)["contract"]["share_basis"]
    assert runner.invoke(cli, ["formulas", "not_an_indicator"]).exit_code == 2
    assert "Preço" in runner.invoke(cli, ["formulas", "pe_basic"]).output


async def test_api_formula_catalog_and_unknown_name_without_lifespan() -> None:
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        result = await client.get("/formulas")
        assert result.status_code == 200
        assert len(result.json()) == len(public_indicator_names())
        detail = await client.get("/formulas/pe_basic")
        assert detail.status_code == 200
        assert detail.json()["contract"]["numerator"] == "security_price"
        assert (await client.get("/formulas/eps_basic_market")).status_code == 404
        health = await client.get("/health")
        assert health.status_code == 200
        assert health.json()["status"] == "ok"
