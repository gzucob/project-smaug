"""Raw input admission, resumability and price/identity source parity."""

import io
import os
import zipfile
from collections.abc import Iterable, Mapping
from dataclasses import replace
from pathlib import Path
from uuid import uuid4

import pytest
from beanie import init_beanie
from pymongo import AsyncMongoClient
from pymongo.errors import DuplicateKeyError

from smaug.analysis.infrastructure.b3_prices import _reduce
from smaug.ingestion.application.source_inputs import MirrorSourceInputs
from smaug.ingestion.domain.source_inputs import SourceInputArchive, SourceInputRow
from smaug.ingestion.infrastructure.models import (
    SourceInputArchiveDocument,
    SourceInputRowDocument,
)
from smaug.ingestion.infrastructure.source_inputs import (
    LocalSourceInputReader,
    MongoSourceInputRepository,
)


class MemoryInputs:
    def __init__(self) -> None:
        self.archives: dict[tuple[str, str], Mapping[str, int]] = {}
        self.rows: dict[tuple[str, str, str, int], SourceInputRow] = {}
        self.interrupt_after: int | None = None

    async def completed(self, archive: SourceInputArchive) -> bool:
        return (archive.artifact_id, archive.parser_version) in self.archives

    async def write_rows(
        self, archive: SourceInputArchive, rows: Iterable[SourceInputRow]
    ) -> None:
        for count, row in enumerate(rows, 1):
            self.rows.setdefault(
                (archive.artifact_id, archive.parser_version, row.member, row.ordinal),
                row,
            )
            if count == self.interrupt_after:
                raise RuntimeError("interrupted")

    async def complete(
        self, archive: SourceInputArchive, member_counts: Mapping[str, int]
    ) -> None:
        self.archives[(archive.artifact_id, archive.parser_version)] = member_counts


def make_archive(path: Path, members: Mapping[str, str]) -> Path:
    with zipfile.ZipFile(path, "w") as archive:
        for name, text in members.items():
            archive.writestr(name, text.encode("latin-1"))
    return path


def tape_archive(tmp_path: Path) -> Path:
    lines = Path("tests/fixtures/cotahist_sample.txt").read_text().splitlines()
    # The fixture samples an official file: update its trailer to the sample size.
    lines[-1] = lines[-1][:31] + f"{len(lines):011d}" + lines[-1][42:]
    return make_archive(
        tmp_path / "COTAHIST_A2015.ZIP",
        {
            "COTAHIST_A2015.TXT": "\n".join(lines) + "\n",
        },
    )


async def test_raw_tape_preserves_all_price_and_succession_evidence(
    tmp_path: Path,
) -> None:
    path = tape_archive(tmp_path)
    reader = LocalSourceInputReader(path)
    archive = await reader.inspect()
    rows = list(reader.rows())
    assert archive.dataset == "COTAHIST"
    assert archive.artifact_id == f"sha256:{archive.sha256}"
    assert archive.byte_size == path.stat().st_size
    with zipfile.ZipFile(path) as source:
        source_lines = source.read("COTAHIST_A2015.TXT").decode("latin-1").splitlines()
    assert [row.fields["record"] for row in rows] == source_lines
    spot = [row for row in rows if row.fields["TPMERC"] == "010"]
    assert {row.fields["CODNEG"].strip() for row in spot} == set(_reduce(path))
    cegr = [row.fields for row in spot if row.fields["CODNEG"].strip() == "CEGR3"]
    # A quote factor of 1000 remains 1000; no restatement or dividend adjustment.
    assert cegr[0]["FATCOT"] == "0001000"
    assert cegr[0]["DISMES"] != cegr[1]["DISMES"]
    assert cegr[0]["PREULT"] == source_lines[-3][108:121]
    assert cegr[0]["CODISI"] == source_lines[-3][230:242]
    assert rows[0].fields["TIPREG"] == "00"
    assert rows[-1].fields["TIPREG"] == "99"
    restored = tmp_path / "restored" / "COTAHIST_A2015.ZIP"
    restored.parent.mkdir()
    make_archive(
        restored,
        {
            rows[0].member: "\n".join(row.fields["record"] for row in rows) + "\n",
        },
    )
    # Reconcile every price, session, rights and identity transition the existing
    # analysis derives, using only the persisted representation's source facts.
    assert _reduce(restored) == _reduce(path)


async def test_idempotent_replay_resume_and_republication(tmp_path: Path) -> None:
    path = tape_archive(tmp_path)
    original = path.read_bytes()
    repository = MemoryInputs()
    use_case = MirrorSourceInputs(repository)
    repository.interrupt_after = 3
    with pytest.raises(RuntimeError, match="interrupted"):
        await use_case.execute(LocalSourceInputReader(path))
    assert not repository.archives
    repository.interrupt_after = None
    assert await use_case.execute(LocalSourceInputReader(path))
    count = len(repository.rows)
    assert not await use_case.execute(LocalSourceInputReader(path))
    assert len(repository.rows) == count
    assert path.read_bytes() == original
    # A republication under the same filename is a new immutable version.
    with zipfile.ZipFile(path) as archive:
        text = archive.read("COTAHIST_A2015.TXT").decode("latin-1")
    make_archive(path, {"COTAHIST_A2015.TXT": text.replace("PETROBRAS", "PETROBRAX")})
    assert await use_case.execute(LocalSourceInputReader(path))
    assert len(repository.archives) == 2
    assert len(repository.rows) == 2 * count


async def test_fca_retains_versions_names_security_classes_and_empty_values(
    tmp_path: Path,
) -> None:
    path = make_archive(
        tmp_path / "hash.zip",
        {
            "fca_cia_aberta_geral_2026.csv": (
                "CNPJ_Companhia;Codigo_CVM;Nome_Empresarial;Versao\n"
                "00.000.000/0001-00;123;Companhia Antiga;1\n"
                "00.000.000/0001-00;123;Companhia Nova;2\n"
            ),
            "fca_cia_aberta_valor_mobiliario_2026.csv": (
                "CNPJ_Companhia;Codigo_Negociacao;Valor_Mobiliario;Mercado\n"
                "00.000.000/0001-00;AAAA3;Ações Ordinárias;Bolsa\n"
                "00.000.000/0001-00;BBBB4;Ações Preferenciais;\n"
            ),
        },
    )
    reader = LocalSourceInputReader(path)
    metadata = await reader.inspect()
    rows = list(reader.rows())
    assert metadata.year == 2026
    assert [row.fields.get("Nome_Empresarial") for row in rows[:2]] == [
        "Companhia Antiga",
        "Companhia Nova",
    ]
    assert rows[-1].fields["Mercado"] == ""
    assert rows[-1].fields["Valor_Mobiliario"] == "Ações Preferenciais"
    assert sum(reader.member_counts().values()) == 4


@pytest.mark.parametrize(
    "text",
    [
        "short\n",
        "01" + " " * 243 + "\n",
    ],
)
async def test_invalid_tape_never_gets_admitted(tmp_path: Path, text: str) -> None:
    path = make_archive(tmp_path / "bad.zip", {"COTAHIST_A2026.TXT": text})
    repository = MemoryInputs()
    with pytest.raises(ValueError, match="[Ii]nvalid"):
        await MirrorSourceInputs(repository).execute(LocalSourceInputReader(path))
    assert not repository.archives


@pytest.mark.parametrize("problem", ["missing_trailer", "record_count", "session_year"])
async def test_incomplete_or_inconsistent_tape_is_not_admitted(
    tmp_path: Path,
    problem: str,
) -> None:
    path = tape_archive(tmp_path)
    with zipfile.ZipFile(path) as source:
        lines = source.read("COTAHIST_A2015.TXT").decode("latin-1").splitlines()
    if problem == "missing_trailer":
        lines.pop()
    elif problem == "record_count":
        lines[-1] = lines[-1][:31] + "00000000001" + lines[-1][42:]
    else:
        lines[1] = lines[1][:2] + "2016" + lines[1][6:]
    make_archive(path, {"COTAHIST_A2015.TXT": "\n".join(lines) + "\n"})
    repository = MemoryInputs()
    with pytest.raises(ValueError, match="incomplete|invalid"):
        await MirrorSourceInputs(repository).execute(LocalSourceInputReader(path))
    assert not repository.archives


async def test_ragged_cvm_rows_preserve_missing_columns_and_extra_cells(
    tmp_path: Path,
) -> None:
    path = make_archive(
        tmp_path / "bad.zip",
        {
            "fca_cia_aberta_geral_2026.csv": "CNPJ;Nome\n123;Nome;extra\n456\n789;\n",
        },
    )
    repository = MemoryInputs()
    assert await MirrorSourceInputs(repository).execute(LocalSourceInputReader(path))
    rows = list(repository.rows.values())
    assert rows[0].columns == ("CNPJ", "Nome")
    assert rows[0].cells == ("123", "Nome", "extra")
    assert "Nome" not in rows[1].fields
    assert rows[1].cells == ("456",)
    assert rows[2].fields["Nome"] == ""


async def test_rejects_unknown_and_mixed_archives(tmp_path: Path) -> None:
    for members in (
        {"cached.json": "{}"},
        {"fca_cia_aberta_geral_2026.csv": "A\n", "dfp_cia_aberta_2025.csv": "A\n"},
    ):
        path = make_archive(tmp_path / "bad.zip", members)
        with pytest.raises(ValueError, match="unsupported or mixed"):
            await LocalSourceInputReader(path).inspect()


async def test_source_changes_do_not_mix_captured_archive_versions(
    tmp_path: Path,
) -> None:
    path = tape_archive(tmp_path)
    reader = LocalSourceInputReader(path)
    metadata = await reader.inspect()
    with path.open("ab") as stream:
        stream.write(b"changed")
    rows = list(reader.rows())
    reader.close()
    assert rows[-1].fields["TIPREG"] == "99"
    new_reader = LocalSourceInputReader(path)
    changed = await new_reader.inspect()
    new_reader.close()
    assert metadata.artifact_id != changed.artifact_id


async def test_parser_version_is_part_of_admission_identity(tmp_path: Path) -> None:
    reader = LocalSourceInputReader(tape_archive(tmp_path))
    archive = await reader.inspect()
    repository = MemoryInputs()
    await repository.complete(archive, {})
    assert await repository.completed(archive)
    assert not await repository.completed(replace(archive, parser_version="v2"))
    reader.close()


def test_invalid_csv_column_names_are_rejected() -> None:
    with pytest.raises(ValueError, match="duplicate"):
        list(LocalSourceInputReader._csv_rows(io.StringIO("A;A\n1;2\n")))


@pytest.mark.skipif(
    not os.environ.get("SMAUG_TEST_MONGO_URI"),
    reason="Set SMAUG_TEST_MONGO_URI to test source inputs in an isolated database",
)
async def test_mongo_admission_bulk_resume_indexes_and_source_versions(
    tmp_path: Path,
) -> None:
    client = AsyncMongoClient(os.environ["SMAUG_TEST_MONGO_URI"])
    database = client["source_inputs_test_" + uuid4().hex]
    try:
        await init_beanie(
            database=database,
            document_models=[
                SourceInputArchiveDocument,
                SourceInputRowDocument,
            ],
        )
        repository = MongoSourceInputRepository()
        use_case = MirrorSourceInputs(repository)
        path = tape_archive(tmp_path)
        original = path.read_bytes()
        reader = LocalSourceInputReader(path)
        metadata = await reader.inspect()
        assert await use_case.execute(reader)
        count = await database.source_input_rows.count_documents({})
        assert not await use_case.execute(LocalSourceInputReader(path))
        assert await database.source_input_rows.count_documents({}) == count
        assert path.read_bytes() == original
        saved = await database.source_input_rows.find_one({"code": "CEGR3"})
        assert saved is not None
        assert saved["fields"]["FATCOT"] == "0001000"
        admitted = await database.source_input_archives.find_one({})
        assert admitted is not None
        assert admitted["price_basis"] == "as_traded"
        assert admitted["sha256"] == metadata.sha256
        with pytest.raises(DuplicateKeyError):
            await database.source_input_rows.insert_one(
                {key: value for key, value in saved.items() if key != "_id"}
            )

        # Simulate an interruption after the first committed bulk of 1000 rows.
        csv_path = make_archive(
            tmp_path / "fca.zip",
            {
                "fca_cia_aberta_geral_2026.csv": "Codigo_CVM;Nome_Empresarial\n"
                + "123;Companhia\n" * 1005,
            },
        )
        csv_reader = LocalSourceInputReader(csv_path)
        csv_metadata = await csv_reader.inspect()
        rows = list(csv_reader.rows())
        await repository.write_rows(csv_metadata, rows[:1000])
        assert not await repository.completed(csv_metadata)
        assert await use_case.execute(LocalSourceInputReader(csv_path))
        assert (
            await database.source_input_rows.count_documents(
                {
                    "artifact_id": csv_metadata.artifact_id,
                }
            )
            == 1005
        )
        assert not await use_case.execute(LocalSourceInputReader(csv_path))

        # Same filename, amended public bytes; both source versions survive.
        make_archive(
            csv_path,
            {
                "fca_cia_aberta_geral_2026.csv": (
                    "Codigo_CVM;Nome_Empresarial\n123;Nova\n"
                ),
            },
        )
        assert await use_case.execute(LocalSourceInputReader(csv_path))
        assert await database.source_input_archives.count_documents({}) == 3
        assert await database.source_input_rows.count_documents({}) == count + 1006
    finally:
        await client.drop_database(database.name)
        await client.close()
