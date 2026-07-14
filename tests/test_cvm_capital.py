"""CvmCapitalSource: mirrors the paid-in capital row from CVM's yearly FRE zip."""

import csv
import io
import zipfile
from pathlib import Path

import httpx
import pytest

from smaug.ingestion.infrastructure.cvm_capital import (
    CAPITAL_MODULE,
    CvmCapitalSource,
)
from smaug.shared.errors import BrapiNotFoundError

_PETRO = "33.000.167/0001-01"
_VALE = "33.592.510/0001-54"

_COLUMNS = (
    "CNPJ_Companhia",
    "Data_Referencia",
    "Versao",
    "ID_Documento",
    "Nome_Companhia",
    "ID_Capital_Social",
    "Tipo_Capital",
    "Data_Autorizacao_Aprovacao",
    "Valor_Capital",
    "Prazo_Integralizacao",
    "Quantidade_Acoes_Ordinarias",
    "Quantidade_Acoes_Preferenciais",
    "Quantidade_Total_Acoes",
)


def _row(
    cnpj: str,
    *,
    version: str,
    capital_type: str = "Capital Integralizado",
    common: str = "10",
    preferred: str = "5",
    total: str = "15",
) -> dict[str, str]:
    return {
        "CNPJ_Companhia": cnpj,
        "Data_Referencia": "2025-12-31",
        "Versao": version,
        "ID_Documento": "1",
        "Nome_Companhia": "COMPANHIA TESTE S.A.",
        "ID_Capital_Social": "1",
        "Tipo_Capital": capital_type,
        "Data_Autorizacao_Aprovacao": "2023-04-27",
        "Valor_Capital": "1000.00",
        "Prazo_Integralizacao": "",
        "Quantidade_Acoes_Ordinarias": common,
        "Quantidade_Acoes_Preferenciais": preferred,
        "Quantidade_Total_Acoes": total,
    }


def _write_zip(path: Path, rows: list[dict[str, str]], year: int = 2025) -> None:
    buffer = io.StringIO()
    writer = csv.DictWriter(
        buffer, fieldnames=list(_COLUMNS), delimiter=";", lineterminator="\n"
    )
    writer.writeheader()
    for row in rows:
        writer.writerow(row)
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr(
            f"fre_cia_aberta_capital_social_{year}.csv",
            buffer.getvalue().encode("latin-1"),
        )


def _source(cache_dir: Path, **kwargs: object) -> CvmCapitalSource:
    return CvmCapitalSource(
        httpx.AsyncClient(),
        {"PETR4": _PETRO, "VALE3": _VALE},
        year=2025,
        cache_dir=str(cache_dir),
        **kwargs,  # type: ignore[arg-type]
    )


async def test_fetch_mirrors_the_paid_in_share_counts(tmp_path: Path) -> None:
    _write_zip(
        tmp_path / "fre_cia_aberta_2025.zip",
        [
            # Issued/subscribed carry different counts and must be ignored.
            _row(_PETRO, version="1", capital_type="Capital Emitido", total="999"),
            _row(
                _PETRO,
                version="1",
                common="7442231382",
                preferred="5446501379",
                total="12888732761",
            ),
        ],
    )

    results = await _source(tmp_path).fetch("PETR4", CAPITAL_MODULE)

    assert len(results) == 1
    payload = results[0].payload
    assert payload["capital_type"] == "Capital Integralizado"
    assert payload["common_shares"] == 7442231382
    assert payload["preferred_shares"] == 5446501379
    assert payload["total_shares"] == 12888732761
    assert payload["reference_date"] == "2025-12-31"
    assert results[0].request["cnpj"] == _PETRO


async def test_fetch_mirrors_every_filed_version_and_picks_none(tmp_path: Path) -> None:
    # ADR 0016: which amendment supersedes which is the reader's call, not the
    # mirror's. Both are stored; ``MongoCapitalReader`` takes the highest version.
    _write_zip(
        tmp_path / "fre_cia_aberta_2025.zip",
        [
            _row(_PETRO, version="22", total="200"),
            _row(_PETRO, version="3", total="100"),
        ],
    )

    results = await _source(tmp_path).fetch("PETR4", CAPITAL_MODULE)

    filed = {r.payload["version"]: r.payload["total_shares"] for r in results}
    assert filed == {22: 200, 3: 100}
    assert all(r.request["version"] == r.payload["version"] for r in results)


async def test_fetch_raises_for_a_company_absent_from_the_file(tmp_path: Path) -> None:
    _write_zip(tmp_path / "fre_cia_aberta_2025.zip", [_row(_PETRO, version="1")])

    with pytest.raises(BrapiNotFoundError):
        await _source(tmp_path).fetch("VALE3", CAPITAL_MODULE)


async def test_fetch_raises_for_an_unmapped_ticker(tmp_path: Path) -> None:
    _write_zip(tmp_path / "fre_cia_aberta_2025.zip", [_row(_PETRO, version="1")])

    with pytest.raises(BrapiNotFoundError):
        await _source(tmp_path).fetch("WEGE3", CAPITAL_MODULE)
