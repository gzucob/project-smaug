"""Official rights ingestion keeps raw rows and rejects identity/schema drift."""

import base64
import csv
import io
import json
import zipfile
from pathlib import Path

import httpx
import pytest

from smaug.ingestion.infrastructure.b3_listing import B3ListingSource
from smaug.ingestion.infrastructure.cvm_capital import CvmCapitalSource
from smaug.ingestion.infrastructure.cvm_tag_along import CvmTagAlongSource
from smaug.shared.errors import (
    SourceBatchValidationError,
    SourceMalformedError,
    SourceNotFoundError,
)


async def test_cvm_rights_mirror_species_versions_and_zero_without_calculation(
    tmp_path: Path,
) -> None:
    row = {
        "CNPJ_Companhia": "12.000.000/0001-00",
        "Data_Referencia": "2022-01-01",
        "Versao": "1",
        "ID_Documento": "10",
        "Especie_Acao": "Ordinária",
        "Classe_Acao_Preferencial": "",
        "Percentual_Tag_Along": "100.000000",
    }
    rows = [
        row,
        {**row, "Versao": "2", "Percentual_Tag_Along": "80.000000"},
        {**row, "Especie_Acao": "Preferencial", "Percentual_Tag_Along": "0.000000"},
    ]
    output = io.StringIO()
    writer = csv.DictWriter(output, fieldnames=list(row), delimiter=";")
    writer.writeheader()
    writer.writerows(rows)
    filing_output = io.StringIO()
    filing_fields = ["CNPJ_CIA", "DT_REFER", "VERSAO", "ID_DOC", "DT_RECEB"]
    filing_writer = csv.DictWriter(
        filing_output, fieldnames=filing_fields, delimiter=";"
    )
    filing_writer.writeheader()
    for version in ["1", "2"]:
        filing_writer.writerow(
            dict(
                zip(
                    filing_fields,
                    [
                        row["CNPJ_Companhia"],
                        row["Data_Referencia"],
                        version,
                        "10",
                        "2022-05-31",
                    ],
                    strict=True,
                )
            )
        )
    with zipfile.ZipFile(tmp_path / "fre_cia_aberta_2022.zip", "w") as z:
        z.writestr("fre_cia_aberta_2022.csv", filing_output.getvalue())
        z.writestr(
            "fre_cia_aberta_direito_acao_2022.csv", output.getvalue().encode("latin-1")
        )
    async with httpx.AsyncClient() as http:
        cnpjs = {"TEST3": row["CNPJ_Companhia"]}
        archive = CvmCapitalSource(http, cnpjs, year=2022, cache_dir=str(tmp_path))
        source = CvmTagAlongSource(archive, cnpjs, {"TEST3": "42"}, year=2022)
        results = await source.fetch("TEST3", "TAG_ALONG")
    assert [
        {k: v for k, v in r.payload.items() if k != "filing"} for r in results
    ] == rows
    assert all(r.payload["filing"]["DT_RECEB"] == "2022-05-31" for r in results)
    assert all(r.cvm_code == "42" and r.source == "cvm" for r in results)
    assert len({json.dumps(dict(r.request), sort_keys=True) for r in results}) == 3


@pytest.mark.parametrize("member_exists", [True, False])
async def test_missing_member_or_tag_header_does_not_produce_inferred_rights(
    tmp_path: Path, member_exists: bool
) -> None:
    with zipfile.ZipFile(tmp_path / "fre_cia_aberta_2022.zip", "w") as z:
        z.writestr(
            "fre_cia_aberta_direito_acao_2022.csv"
            if member_exists
            else "unrelated.csv",
            "CNPJ_Companhia;Data_Referencia\n12.000.000/0001-00;2022-01-01\n",
        )
    async with httpx.AsyncClient() as http:
        cnpjs = {"TEST3": "12.000.000/0001-00"}
        archive = CvmCapitalSource(http, cnpjs, year=2022, cache_dir=str(tmp_path))
        source = CvmTagAlongSource(archive, cnpjs, {"TEST3": "42"}, year=2022)
        with pytest.raises(
            SourceBatchValidationError if member_exists else SourceNotFoundError
        ):
            await source.fetch("TEST3", "TAG_ALONG")


@pytest.mark.parametrize("wrong_identity", [False, True])
async def test_b3_listing_preserves_raw_classification_and_checks_the_registrant(
    wrong_identity: bool,
) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        params = json.loads(base64.b64decode(request.url.path.rsplit("/", 1)[1]))
        if "GetDetail" in request.url.path:
            assert params["codeCVM"] == "42"
            return httpx.Response(
                200,
                json={
                    "codeCVM": "99" if wrong_identity else "42",
                    "issuingCompany": "TEST",
                    "cnpj": "12000000000100",
                    "market": "BOVESPA NIVEL 2",
                },
            )
        assert params["issuingCompany"] == "TEST"
        return httpx.Response(
            200,
            json=[
                {
                    "codeCVM": "42",
                    "code": "TEST",
                    "tradingName": "TEST COMPANY",
                    "segment": "N2",
                    "stockDividends": [],
                }
            ],
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        source = B3ListingSource(
            http,
            {"TEST3": "42"},
            {"TEST3": "12.000.000/0001-00"},
            base_url="https://b3.test",
        )
        if wrong_identity:
            with pytest.raises(SourceMalformedError):
                await source.fetch("TEST3", "LISTING_B3")
        else:
            (result,) = await source.fetch("TEST3", "LISTING_B3")
            assert result.payload["supplement"]["segment"] == "N2"
            assert result.payload["detail"]["market"] == "BOVESPA NIVEL 2"
            assert result.payload["observed_at"]
            assert "tag_along" not in result.payload
