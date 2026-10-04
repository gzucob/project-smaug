"""FRE free-float ingestion preserves versions and raw class distributions."""

import csv
import io
import zipfile
from pathlib import Path

import httpx

from smaug.ingestion.infrastructure.cvm_capital import CvmCapitalSource
from smaug.ingestion.infrastructure.cvm_free_float import CvmFreeFloatSource


def _csv(rows: list[dict[str, str]]) -> bytes:
    buffer = io.StringIO()
    writer = csv.DictWriter(buffer, fieldnames=list(rows[0]), delimiter=";")
    writer.writeheader()
    writer.writerows(rows)
    return buffer.getvalue().encode("latin-1")


async def test_free_float_mirrors_versions_and_matches_the_class_identity(
    tmp_path: Path,
) -> None:
    identity = {
        "CNPJ_Companhia": "33.000.167/0001-01",
        "Data_Referencia": "2025-12-31",
        "Versao": "1",
        "ID_Documento": "10",
        "Nome_Companhia": "Companhia Teste",
    }
    first = {
        **identity,
        "Percentual_Total_Acoes_Circulacao": "45.50",
        "Data_Ultima_Assembleia": "2025-04-30",
    }
    amended = {**first, "Versao": "2", "Percentual_Total_Acoes_Circulacao": "46.00"}
    share_class = {**identity, "Tipo_Classe": "ON", "Percentual": "40.00"}
    with zipfile.ZipFile(tmp_path / "fre_cia_aberta_2025.zip", "w") as archive:
        archive.writestr(
            "fre_cia_aberta_distribuicao_capital_2025.csv", _csv([first, amended])
        )
        archive.writestr(
            "fre_cia_aberta_distribuicao_capital_classe_acao_2025.csv",
            _csv([share_class]),
        )
    mapping = {"PETR4": identity["CNPJ_Companhia"]}
    async with httpx.AsyncClient() as http:
        transport = CvmCapitalSource(http, mapping, year=2025, cache_dir=str(tmp_path))
        source = CvmFreeFloatSource(transport, mapping, {"PETR4": "9512"}, year=2025)
        results = await source.fetch("PETR4", "FREE_FLOAT")
    assert len(results) == 2
    assert results[0].payload["company_free_float_percent"] == "45.50"
    assert results[1].payload["company_free_float_percent"] == "46.00"
    assert results[0].payload["filed_fields"] == first
    assert results[1].payload["filed_fields"] == amended
    assert results[0].payload["share_classes"] == [share_class]
    assert results[1].payload["share_classes"] == []
    assert results[0].cvm_code == "9512"
