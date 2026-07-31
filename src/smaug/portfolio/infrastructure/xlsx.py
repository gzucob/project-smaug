"""Minimal read-only XLSX reader — enough for one B3 spreadsheet.

An ``.xlsx`` is a zip of XML: the cells live in ``xl/worksheets/sheet1.xml`` and
most of their text is not in there at all but in ``xl/sharedStrings.xml``, which
the cells index into. That is the whole format, for the purpose of reading a
table of strings.

Written here rather than pulled in, for the reason ADR 0009 gives for reading
CVM's CSVs directly instead of through pycvm: the library we replaced did not
fail loudly when the file surprised it — it returned wrong data, and cost #55.
Forty lines we can read beats a dependency we cannot, for a format whose
producer may change it.

Deliberately partial: no formulas, no number formats, no dates, no styles. It
reads a rectangle of text and says so.
"""

from __future__ import annotations

import re
import zipfile
from pathlib import Path
from xml.etree import ElementTree

_NS = {"x": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}
_CELL_REF = re.compile(r"^([A-Z]+)")


def _column_index(reference: str) -> int:
    """``"B4"`` -> 1. Spreadsheet columns are base-26 with no zero."""
    letters = _CELL_REF.match(reference)
    if letters is None:
        return 0
    index = 0
    for char in letters.group(1):
        index = index * 26 + (ord(char) - ord("A") + 1)
    return index - 1


def _shared_strings(archive: zipfile.ZipFile) -> list[str]:
    """The workbook's string table, which most text cells point into."""
    try:
        raw = archive.read("xl/sharedStrings.xml")
    except KeyError:
        return []
    root = ElementTree.fromstring(raw)
    strings: list[str] = []
    for item in root.findall("x:si", _NS):
        # A string can be split across runs (<r><t>…</t></r>) when part of it is
        # styled differently; the value is every run's text, in order.
        strings.append(
            "".join(node.text or "" for node in item.iter() if _is_text(node))
        )
    return strings


def _is_text(node: ElementTree.Element) -> bool:
    return node.tag == f"{{{_NS['x']}}}t"


def read_rows(
    path: Path, *, sheet: str = "xl/worksheets/sheet1.xml"
) -> list[list[str]]:
    """Every row of ``sheet`` as a list of cell strings, empty cells included.

    Rows are padded to the widest cell reference seen in that row, so a caller
    can index by column without checking the length of each one.
    """
    with zipfile.ZipFile(path) as archive:
        strings = _shared_strings(archive)
        root = ElementTree.fromstring(archive.read(sheet))

    rows: list[list[str]] = []
    for row_node in root.iter(f"{{{_NS['x']}}}row"):
        row: list[str] = []
        for cell in row_node.findall("x:c", _NS):
            index = _column_index(cell.get("r", ""))
            while len(row) < index:
                row.append("")
            row.append(_cell_text(cell, strings))
        rows.append(row)
    return rows


def _cell_text(cell: ElementTree.Element, strings: list[str]) -> str:
    value = cell.find("x:v", _NS)
    if value is None or value.text is None:
        # An inline string carries its text in the cell instead of the table.
        inline = cell.find("x:is", _NS)
        if inline is None:
            return ""
        return "".join(node.text or "" for node in inline.iter() if _is_text(node))
    if cell.get("t") == "s":
        try:
            return strings[int(value.text)]
        except (ValueError, IndexError):
            return ""
    return value.text
