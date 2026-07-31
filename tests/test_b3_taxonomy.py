"""Reading B3's classification: spreadsheet first, per-company detail as fallback.

No network. A transport stub answers both endpoints, and the spreadsheet is
built here as a real ``.xlsx`` so the stdlib reader is exercised on the format it
will actually meet — merged level columns included, since B3 leaves them blank
on every row but the first of a group.
"""

from __future__ import annotations

import base64
import json
import zipfile
from io import BytesIO
from pathlib import Path

import httpx

from smaug.portfolio.application.refresh_taxonomy import (
    RefreshTaxonomyUseCase,
    snapshot_payload,
)
from smaug.portfolio.domain.taxonomy import Classification
from smaug.portfolio.domain.universe import ListedCompany
from smaug.portfolio.infrastructure.b3_taxonomy import B3TaxonomySource

_SHEET_XML = """<?xml version="1.0"?>
<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">
<sheetData>{rows}</sheetData></worksheet>"""


def _xlsx(rows: list[list[str]]) -> bytes:
    """A minimal real .xlsx: inline strings, no shared-string table."""
    body = []
    for index, row in enumerate(rows, start=1):
        cells = []
        for column, value in enumerate(row):
            ref = f"{chr(ord('A') + column)}{index}"
            if value:
                cells.append(f'<c r="{ref}" t="inlineStr"><is><t>{value}</t></is></c>')
        body.append(f'<row r="{index}">{"".join(cells)}</row>')
    buffer = BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr(
            "xl/worksheets/sheet1.xml", _SHEET_XML.format(rows="".join(body))
        )
    return buffer.getvalue()


# Columns B..F: SETOR, SUBSETOR, SEGMENTO, NOME DE PREGÃO, CÓDIGO. B3 merges the
# three level columns down a group, so only the first row of one carries them.
_ROWS = [
    ["", "SETOR", "SUBSETOR", "SEGMENTO", "EMISSOR", ""],
    ["", "", "", "", "NOME DE PREGÃO", "CÓDIGO"],
    ["", "Bens Industriais", "Máquinas e Equipamentos", "Motores", "WEG", "WEGE"],
    ["", "", "", "", "SCHULZ", "SHUL"],
    ["", "Financeiro", "Bancos", "Bancos", "BCO BRASIL", "BBAS"],
]


def _company(cd_cvm: str, *tickers: str) -> ListedCompany:
    return ListedCompany(
        cd_cvm=cd_cvm,
        cnpj="00.000.000/0001-00",
        denom="COMPANHIA TESTE S.A.",
        ticker=tickers[0],
        tickers=tickers,
    )


def _source(
    *, sheet: list[list[str]] | None = None, details: dict[str, str] | None = None
) -> B3TaxonomySource:
    """A source whose two endpoints answer from ``sheet`` and ``details``."""
    payloads = details or {}

    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if "GetDownloadIndustryClassification" in path:
            return httpx.Response(200, content=_xlsx(sheet or _ROWS))
        code = json.loads(base64.b64decode(path.rsplit("/", 1)[-1]))["codeCVM"]
        industry = payloads.get(code)
        if industry is None:
            # How the endpoint says "no such listed company": 200, empty body.
            return httpx.Response(200, text="")
        return httpx.Response(200, json={"industryClassification": industry})

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    return B3TaxonomySource(client)


async def test_the_spreadsheet_classifies_every_class_of_a_company() -> None:
    fetched = await _source().fetch((_company("5410", "WEGE3", "WEGE4"),))

    expected = Classification("Bens Industriais", "Máquinas e Equipamentos", "Motores")
    assert fetched.classifications == {"WEGE3": expected, "WEGE4": expected}
    assert fetched.from_detail == 0


async def test_merged_level_columns_carry_down_the_group() -> None:
    """SCHULZ's row is blank in the three level columns; it is not unclassified."""
    fetched = await _source().fetch((_company("1", "SHUL4"),))

    assert fetched.classifications["SHUL4"] == Classification(
        "Bens Industriais", "Máquinas e Equipamentos", "Motores"
    )


async def test_a_renamed_company_falls_back_to_the_registrant_key() -> None:
    # Eletrobras is on the spreadsheet as AXIA; the CVM archive we ingested still
    # says ELET. The root join cannot reach it, the CD_CVM one can.
    fetched = await _source(
        details={"2437": "Utilidade Pública / Energia Elétrica / Energia Elétrica"}
    ).fetch((_company("2437", "ELET3", "ELET6"),))

    assert fetched.from_sheet == 0
    assert fetched.from_detail == 2
    assert fetched.classifications["ELET6"].setor == "Utilidade Pública"


async def test_the_fallbacks_comma_defect_is_corrected() -> None:
    raw = (
        "Petróleo. Gás e Biocombustíveis / Petróleo. Gás e Biocombustíveis"
        " / Exploração. Refino e Distribuição"
    )
    sheet = [
        *_ROWS,
        [
            "",
            "Petróleo, Gás e Biocombustíveis",
            "Petróleo, Gás e Biocombustíveis",
            "Exploração, Refino e Distribuição",
            "BRAVA",
            "BRAV",
        ],
    ]
    fetched = await _source(sheet=sheet, details={"9512": raw}).fetch(
        (_company("9512", "PETR4"),)
    )

    assert fetched.classifications["PETR4"] == Classification(
        "Petróleo, Gás e Biocombustíveis",
        "Petróleo, Gás e Biocombustíveis",
        "Exploração, Refino e Distribuição",
    )
    assert fetched.unknown_labels == ()


async def test_a_fallback_label_outside_b3s_vocabulary_is_reported() -> None:
    """The spreadsheet says what a label may be; anything else wants a human.

    Not a hunt for periods: B3's own labels contain them ("Máq. e Equip."), and a
    rule that flagged those would have to be taught the difference — which is the
    mistake this replaced.
    """
    fetched = await _source(
        details={"1": "Bens Industriais / Setor Que Nao Existe / Motores"}
    ).fetch((_company("1", "AAAA3"),))

    assert fetched.unknown_labels == ("Setor Que Nao Existe",)
    # Reported is not discarded — the ticker is still classified.
    assert fetched.classifications["AAAA3"].subsetor == "Setor Que Nao Existe"


async def test_a_company_neither_source_classifies_is_listed_not_invented() -> None:
    fetched = await _source().fetch((_company("999", "FALI3"),))

    assert fetched.classifications == {}
    assert fetched.unclassified == ("FALI3",)


async def test_a_fallback_reply_without_three_levels_is_refused() -> None:
    fetched = await _source(details={"1": "Só Um Nível"}).fetch(
        (_company("1", "AAAA3"),)
    )

    assert fetched.classifications == {}
    assert fetched.unclassified == ("AAAA3",)


def test_drift_separates_gained_from_lost(tmp_path: Path) -> None:
    snapshot = tmp_path / "b3_taxonomy.json"
    snapshot.write_text(
        snapshot_payload({"ZZZZ9": Classification("A", "B", "C")}), encoding="utf-8"
    )

    drift = RefreshTaxonomyUseCase(snapshot).drift(
        {"YYYY9": Classification("D", "E", "F")}
    )

    assert drift.gained == ("YYYY9",)  # absent from the committed snapshot
    assert drift.lost == ("ZZZZ9",)  # in the file, absent from the fetch
    assert drift.moved


def test_writing_the_snapshot_reproduces_its_own_bytes(tmp_path: Path) -> None:
    """A weekly diff is only readable if a no-op run is a no-op."""
    snapshot = tmp_path / "b3_taxonomy.json"
    entries = {
        "BBBB3": Classification("Financeiro", "Bancos", "Bancos"),
        "AAAA3": Classification("Saúde", "Medicamentos", "Medicamentos"),
    }
    RefreshTaxonomyUseCase(snapshot).write(entries)
    first = snapshot.read_text(encoding="utf-8")
    RefreshTaxonomyUseCase(snapshot).write(entries)

    assert snapshot.read_text(encoding="utf-8") == first
    assert "\\u00e1" not in first  # accents stay legible in the diff
