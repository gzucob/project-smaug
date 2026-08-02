"""CvmCapitalEventSource: the corporate actions CVM declares, mirrored as filed.

The values in these fixtures are Ampla's real 2015 grupamento, which is the case
that exposed why a declared event beats an inferred one: its FRE count ratio
(1/23,539) is the grupamento *and* the share issue that followed compounded into
one number, matching neither event.
"""

import csv
import io
import zipfile
from pathlib import Path

import httpx
import pytest

from smaug.ingestion.infrastructure.cvm_capital import (
    CAPITAL_EVENT_MODULE,
    CvmCapitalEventSource,
)
from smaug.shared.errors import BrapiNotFoundError

_AMPLA = "33.050.071/0001-58"
_VALE = "33.592.510/0001-54"

_COLUMNS = (
    "CNPJ_Companhia",
    "Data_Referencia",
    "Versao",
    "ID_Documento",
    "Nome_Companhia",
    "ID_Capital_Social_Desdobramento",
    "Data_Aprovacao",
    "Tipo_Evento",
    "Quantidade_Acoes_Ordinarias_Antes_Aprovacao",
    "Quantidade_Acoes_Preferenciais_Antes_Aprovacao",
    "Quantidade_Total_Acoes_Antes_Aprovacao",
    "Quantidade_Acoes_Ordinarias_Depois_Aprovacao",
    "Quantidade_Acoes_Preferenciais_Depois_Aprovacao",
    "Quantidade_Total_Acoes_Depois_Aprovacao",
)


def _row(
    cnpj: str,
    *,
    kind: str = "Grupamento",
    approved: str = "2015-12-15",
    before: str = "3922515918446",
    after: str = "98062897",
    event_id: str = "19751",
    version: str = "6",
) -> dict[str, str]:
    return {
        "CNPJ_Companhia": cnpj,
        "Data_Referencia": "2017-01-01",
        "Versao": version,
        "ID_Documento": "73841",
        "Nome_Companhia": "AMPLA ENERGIA E SERVICOS S.A.",
        "ID_Capital_Social_Desdobramento": event_id,
        "Data_Aprovacao": approved,
        "Tipo_Evento": kind,
        "Quantidade_Acoes_Ordinarias_Antes_Aprovacao": before,
        "Quantidade_Acoes_Preferenciais_Antes_Aprovacao": "0",
        "Quantidade_Total_Acoes_Antes_Aprovacao": before,
        "Quantidade_Acoes_Ordinarias_Depois_Aprovacao": after,
        "Quantidade_Acoes_Preferenciais_Depois_Aprovacao": "0",
        "Quantidade_Total_Acoes_Depois_Aprovacao": after,
    }


def _write_zip(
    path: Path,
    rows: list[dict[str, str]],
    *,
    year: int = 2017,
    member: str | None = None,
) -> None:
    buffer = io.StringIO()
    writer = csv.DictWriter(
        buffer, fieldnames=list(_COLUMNS), delimiter=";", lineterminator="\n"
    )
    writer.writeheader()
    for row in rows:
        writer.writerow(row)
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr(
            member or f"fre_cia_aberta_capital_social_desdobramento_{year}.csv",
            buffer.getvalue().encode("latin-1"),
        )


def _source(cache_dir: Path, year: int = 2017) -> CvmCapitalEventSource:
    return CvmCapitalEventSource(
        httpx.AsyncClient(),
        {"CBEE3": _AMPLA, "VALE3": _VALE},
        year=year,
        cache_dir=str(cache_dir),
        ticker_to_code={"CBEE3": "15253"},
    )


async def test_fetch_mirrors_the_declared_event_with_both_sides_of_the_count(
    tmp_path: Path,
) -> None:
    _write_zip(tmp_path / "fre_cia_aberta_2017.zip", [_row(_AMPLA)])

    results = await _source(tmp_path).fetch("CBEE3", CAPITAL_EVENT_MODULE)

    assert len(results) == 1
    payload = results[0].payload
    assert payload["event_type"] == "Grupamento"
    assert payload["approval_date"] == "2015-12-15"
    # 1:40,000 — stated, not deduced. The count ratio across FRE years reads
    # 1/23,539 for this company because a share issue followed the grupamento.
    assert payload["total_before"] == 3_922_515_918_446
    assert payload["total_after"] == 98_062_897
    assert results[0].cvm_code == "15253"


async def test_the_approval_date_identifies_one_event_within_a_filing(
    tmp_path: Path,
) -> None:
    # A company files its whole action history in each FRE, so the request key
    # has to separate them — the same defect #86 fixed for the capital rows.
    _write_zip(
        tmp_path / "fre_cia_aberta_2017.zip",
        [
            _row(_AMPLA, approved="2015-12-15", event_id="19751"),
            _row(
                _AMPLA,
                kind="Desdobramento",
                approved="2019-04-10",
                before="98062897",
                after="196125794",
                event_id="19752",
            ),
        ],
    )

    results = await _source(tmp_path).fetch("CBEE3", CAPITAL_EVENT_MODULE)

    assert len(results) == 2
    assert {r.request["approval_date"] for r in results} == {
        "2015-12-15",
        "2019-04-10",
    }
    assert {r.request["event_id"] for r in results} == {"19751", "19752"}


async def test_a_company_that_declared_no_action_is_not_found(tmp_path: Path) -> None:
    _write_zip(tmp_path / "fre_cia_aberta_2017.zip", [_row(_AMPLA)])

    with pytest.raises(BrapiNotFoundError, match="capital event"):
        await _source(tmp_path).fetch("VALE3", CAPITAL_EVENT_MODULE)


async def test_a_year_whose_archive_dropped_the_member_finds_nothing(
    tmp_path: Path,
) -> None:
    # CVM restructured the FRE for 2024 onward and the member is gone entirely.
    # That is an absence to record, not a crash: those years' events come from B3.
    _write_zip(
        tmp_path / "fre_cia_aberta_2025.zip",
        [_row(_AMPLA)],
        member="fre_cia_aberta_capital_social_2025.csv",
    )

    with pytest.raises(BrapiNotFoundError, match="capital event"):
        await _source(tmp_path, year=2025).fetch("CBEE3", CAPITAL_EVENT_MODULE)


async def test_a_zero_on_either_side_is_still_mirrored(tmp_path: Path) -> None:
    # 21 of 538 declared events carry a zero count. Unusable for a ratio, but it
    # is what the company filed — the mirror stores and the reader judges.
    _write_zip(
        tmp_path / "fre_cia_aberta_2017.zip", [_row(_AMPLA, before="0", after="0")]
    )

    results = await _source(tmp_path).fetch("CBEE3", CAPITAL_EVENT_MODULE)

    assert results[0].payload["total_before"] == 0
    assert results[0].payload["total_after"] == 0
