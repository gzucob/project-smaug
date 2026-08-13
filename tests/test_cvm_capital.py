"""CvmCapitalSource: mirrors the paid-in capital row from CVM's yearly FRE zip."""

import csv
import io
import zipfile
from pathlib import Path

import httpx
import pytest

from smaug.ingestion.infrastructure.cvm_capital import (
    CAPITAL_MODULE,
    TREASURY_MODULE,
    CvmCapitalSource,
    CvmTreasurySource,
)
from smaug.shared.errors import SourceNotFoundError

_PETRO = "33.000.167/0001-01"
_VALE = "33.592.510/0001-54"
_BANRISUL = "92.702.067/0001-96"

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

_CLASS_COLUMNS = (
    "CNPJ_Companhia",
    "Data_Referencia",
    "Versao",
    "ID_Documento",
    "Nome_Companhia",
    "ID_Capital_Social",
    "Tipo_Classe_Acao_Preferencial",
    "Quantidade_Acoes",
)


def _row(
    cnpj: str,
    *,
    version: str,
    capital_type: str = "Capital Integralizado",
    common: str = "10",
    preferred: str = "5",
    total: str = "15",
    approved: str = "2023-04-27",
    capital_id: str = "1",
) -> dict[str, str]:
    return {
        "CNPJ_Companhia": cnpj,
        "Data_Referencia": "2025-12-31",
        "Versao": version,
        "ID_Documento": "1",
        "Nome_Companhia": "COMPANHIA TESTE S.A.",
        "ID_Capital_Social": capital_id,
        "Tipo_Capital": capital_type,
        "Data_Autorizacao_Aprovacao": approved,
        "Valor_Capital": "1000.00",
        "Prazo_Integralizacao": "",
        "Quantidade_Acoes_Ordinarias": common,
        "Quantidade_Acoes_Preferenciais": preferred,
        "Quantidade_Total_Acoes": total,
    }


def _write_zip(
    path: Path,
    rows: list[dict[str, str]],
    year: int = 2025,
    *,
    class_rows: list[dict[str, str]] | None = None,
) -> None:
    buffer = io.StringIO()
    writer = csv.DictWriter(
        buffer, fieldnames=list(_COLUMNS), delimiter=";", lineterminator="\n"
    )
    writer.writeheader()
    for row in rows:
        writer.writerow(row)
    class_buffer = io.StringIO()
    class_writer = csv.DictWriter(
        class_buffer,
        fieldnames=list(_CLASS_COLUMNS),
        delimiter=";",
        lineterminator="\n",
    )
    class_writer.writeheader()
    class_writer.writerows(class_rows or [])
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr(
            f"fre_cia_aberta_capital_social_{year}.csv",
            buffer.getvalue().encode("latin-1"),
        )
        archive.writestr(
            f"fre_cia_aberta_capital_social_classe_acao_{year}.csv",
            class_buffer.getvalue().encode("latin-1"),
        )


def _source(cache_dir: Path, **kwargs: object) -> CvmCapitalSource:
    return CvmCapitalSource(
        httpx.AsyncClient(),
        {"PETR4": _PETRO, "VALE3": _VALE, "BRSR5": _BANRISUL},
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


async def test_fetch_joins_the_filed_pna_and_pnb_counts_by_capital_id(
    tmp_path: Path,
) -> None:
    # CVM FRE 2025 v21, ID 351151: Banrisul files these exact PNA/PNB counts
    # under the capital-by-class member, separate from the aggregate PN count.
    main = _row(
        _BANRISUL,
        version="21",
        common="205064841",
        preferred="203909636",
        total="408974477",
        approved="2026-04-28",
        capital_id="351151",
    )
    classes = [
        {
            "CNPJ_Companhia": _BANRISUL,
            "Data_Referencia": "2025-12-31",
            "Versao": "21",
            "ID_Documento": "157542",
            "Nome_Companhia": "BANCO DO ESTADO DO RIO GRANDE DO SUL SA",
            "ID_Capital_Social": "351151",
            "Tipo_Classe_Acao_Preferencial": label,
            "Quantidade_Acoes": shares,
        }
        for label, shares in (
            ("Preferencial Classe A", "1373091"),
            ("Preferencial Classe B", "202536545"),
        )
    ]
    _write_zip(tmp_path / "fre_cia_aberta_2025.zip", [main], class_rows=classes)

    results = await _source(tmp_path).fetch("BRSR5", CAPITAL_MODULE)

    assert results[0].payload["share_class_counts"] == [
        {"share_class": "Preferencial Classe A", "shares": 1373091},
        {"share_class": "Preferencial Classe B", "shares": 202536545},
    ]


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


async def test_fetch_mirrors_every_approval_of_the_same_version(tmp_path: Path) -> None:
    # A version restates the whole capital history: SANEPAR's 2021 FRE files the
    # 2020 split next to two 2016 approvals, all paid-in (#86). Every one is
    # mirrored, each carrying the approval date the reader picks by.
    _write_zip(
        tmp_path / "fre_cia_aberta_2025.zip",
        [
            _row(_PETRO, version="8", approved="2016-12-19", total="503735173"),
            _row(
                _PETRO,
                version="8",
                approved="2020-03-27",
                total="1511205519",
                capital_id="2",
            ),
        ],
    )

    results = await _source(tmp_path).fetch("PETR4", CAPITAL_MODULE)

    filed = {r.payload["approval_date"]: r.payload["total_shares"] for r in results}
    assert filed == {"2016-12-19": 503735173, "2020-03-27": 1511205519}
    assert {r.request["capital_id"] for r in results} == {"1", "2"}


async def test_fetch_raises_for_a_company_absent_from_the_file(tmp_path: Path) -> None:
    _write_zip(tmp_path / "fre_cia_aberta_2025.zip", [_row(_PETRO, version="1")])

    with pytest.raises(SourceNotFoundError):
        await _source(tmp_path).fetch("VALE3", CAPITAL_MODULE)


async def test_fetch_raises_for_an_unmapped_ticker(tmp_path: Path) -> None:
    _write_zip(tmp_path / "fre_cia_aberta_2025.zip", [_row(_PETRO, version="1")])

    with pytest.raises(SourceNotFoundError):
        await _source(tmp_path).fetch("WEGE3", CAPITAL_MODULE)


async def test_treasury_fetch_reports_not_found_when_the_year_has_no_member(
    tmp_path: Path,
) -> None:
    # CVM began publishing the capital composition partway through the series:
    # the 2019 DFP archive has no ``composicao_capital`` member, the 2020 one
    # does. An absent member must degrade to "not found" for the ticker — raising
    # aborted the whole year, which is how the 2015-2019 backfill failed (#63).
    archive = tmp_path / "dfp_cia_aberta_2019.zip"
    with zipfile.ZipFile(archive, "w") as zf:
        zf.writestr("dfp_cia_aberta_BPA_con_2019.csv", "CNPJ_CIA;DT_REFER\n")

    source = CvmTreasurySource(
        httpx.AsyncClient(),
        {"PETR4": _PETRO},
        year=2019,
        cache_dir=str(tmp_path),
    )

    with pytest.raises(SourceNotFoundError):
        await source.fetch("PETR4", TREASURY_MODULE)
