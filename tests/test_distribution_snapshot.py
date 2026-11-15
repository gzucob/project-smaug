"""Distribution integrity, compatibility and safe restore boundaries."""

from __future__ import annotations

import hashlib
import importlib.util
import io
import json
import lzma
import sys
from pathlib import Path
from typing import Any

import pytest

# The release tooling is intentionally outside the installed application package.
_SPEC = importlib.util.spec_from_file_location(
    "distribution_snapshot", "scripts/distribution_snapshot.py"
)
assert _SPEC is not None
assert _SPEC.loader is not None
snapshot = importlib.util.module_from_spec(_SPEC)
sys.modules[_SPEC.name] = snapshot
_SPEC.loader.exec_module(snapshot)


def make_snapshot(directory: Path) -> Any:
    expected = snapshot.Compatibility(
        code_commit="a" * 40,
        code_sha256="b" * 64,
        alembic_revision="0044",
        calculation_contract="last_traded_price_v1",
    )
    mongo_hashes, sql_hashes = {"facts": "c" * 32}, {"results": "d" * 64}
    payloads = []
    for name in snapshot.PAYLOAD_NAMES:
        path = directory / name
        path.write_bytes(b"logical database payload")
        payloads.append(
            snapshot.Payload(
                name=name,
                size_bytes=path.stat().st_size,
                sha256=snapshot.digest_file(path),
            )
        )
    manifest = snapshot.Manifest(
        format_version=1,
        created_at="2026-11-13T23:00:00+00:00",
        compatibility=expected,
        data_revision=hashlib.sha256(
            json.dumps([mongo_hashes, sql_hashes], sort_keys=True).encode()
        ).hexdigest(),
        source_archives=[],
        source_dates={},
        coverage={"facts": 1, "results": 1},
        mongo_hashes=mongo_hashes,
        sql_hashes=sql_hashes,
        validation={"restore_verified": True},
        payloads=payloads,
        native_size_budget_bytes=sum(p.size_bytes for p in payloads)
        + snapshot.NATIVE_OVERHEAD_BYTES,
    )
    (directory / "manifest.json").write_text(manifest.model_dump_json())
    return expected


def make_builder(directory: Path) -> Any:
    work = directory.with_name(directory.name + "-work")
    work.mkdir()
    return snapshot.Builder(work)


def mutate_manifest(directory: Path, field: str, value: object) -> None:
    path = directory / "manifest.json"
    data = json.loads(path.read_text())
    data[field] = value
    path.write_text(json.dumps(data))


def test_release_commit_may_change_only_when_runtime_content_is_identical(
    tmp_path: Path,
) -> None:
    expected = make_snapshot(tmp_path)
    released = expected.model_copy(update={"code_commit": "f" * 40})
    assert snapshot.verify_payloads(tmp_path, released).coverage["facts"] == 1


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("code_sha256", "e" * 64),
        ("alembic_revision", "0045"),
        ("calculation_contract", "changed_formula"),
        ("source_parser", "future-parser"),
        ("mongo_major", 9),
        ("postgres_major", 18),
    ],
)
def test_rejects_incompatible_snapshot(
    tmp_path: Path,
    field: str,
    value: object,
) -> None:
    expected = make_snapshot(tmp_path).model_copy(update={field: value})
    with pytest.raises(ValueError, match="incompatible"):
        snapshot.verify_payloads(tmp_path, expected)


@pytest.mark.parametrize("replacement", [b"short", b"x" * 24])
def test_detects_truncation_and_same_size_corruption(
    tmp_path: Path,
    replacement: bytes,
) -> None:
    expected = make_snapshot(tmp_path)
    (tmp_path / snapshot.PAYLOAD_NAMES[0]).write_bytes(replacement)
    with pytest.raises(ValueError, match="mismatch"):
        snapshot.verify_payloads(tmp_path, expected)


def test_rejects_symlink_payload(tmp_path: Path) -> None:
    expected = make_snapshot(tmp_path)
    target = tmp_path.with_name(tmp_path.name + "-elsewhere")
    path = tmp_path / snapshot.PAYLOAD_NAMES[0]
    path.rename(target)
    path.symlink_to(target)
    with pytest.raises(ValueError, match="Invalid payload"):
        snapshot.verify_payloads(tmp_path, expected)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("format_version", 2),
        ("data_revision", "0" * 64),
        ("native_size_budget_bytes", 2 * 1024**3),
    ],
)
def test_rejects_wrong_format_revision_or_budget(
    tmp_path: Path,
    field: str,
    value: object,
) -> None:
    expected = make_snapshot(tmp_path)
    mutate_manifest(tmp_path, field, value)
    with pytest.raises(ValueError, match="Unsupported|mismatch|budget"):
        snapshot.verify_payloads(tmp_path, expected)


def test_refuses_arbitrary_payload_paths(tmp_path: Path) -> None:
    expected = make_snapshot(tmp_path)
    path = tmp_path / "manifest.json"
    data = json.loads(path.read_text())
    data["payloads"][0]["name"] = "../personal-backup.gz"
    path.write_text(json.dumps(data))
    with pytest.raises(ValueError, match="Unexpected snapshot payload"):
        snapshot.verify_payloads(tmp_path, expected)


def test_integrity_failure_never_opens_restore_target(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    expected = make_snapshot(tmp_path)
    (tmp_path / snapshot.PAYLOAD_NAMES[0]).write_bytes(b"broken")
    builder = make_builder(tmp_path)
    calls: list[str] = []
    monkeypatch.setattr(snapshot, "compatibility", lambda: expected)
    monkeypatch.setattr(builder, "mongo", lambda *_: calls.append("mongo"))
    try:
        with pytest.raises(ValueError, match="mismatch"):
            builder.restore("target-mongo", "target-pg", tmp_path)
        assert calls == []
    finally:
        builder.log.close()


@pytest.mark.parametrize("mongo_empty", [True, False])
def test_restore_refuses_populated_databases_before_import(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    mongo_empty: bool,
) -> None:
    expected = make_snapshot(tmp_path)
    builder = make_builder(tmp_path)
    imports: list[object] = []
    monkeypatch.setattr(snapshot, "compatibility", lambda: expected)
    monkeypatch.setattr(builder, "mongo", lambda *_: [] if mongo_empty else ["facts"])
    monkeypatch.setattr(builder, "sql", lambda *_: "portfolio")
    monkeypatch.setattr(builder, "stream", lambda *a, **kw: imports.append(a))
    try:
        with pytest.raises(ValueError, match="not empty"):
            builder.restore("target-mongo", "target-pg", tmp_path)
        assert imports == []
    finally:
        builder.log.close()


def test_clone_releases_source_write_lock_after_copy_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    builder = make_builder(tmp_path)
    lock_commands: list[str] = []
    monkeypatch.setattr(
        builder, "mongo", lambda _, command, *args: lock_commands.append(command)
    )

    def fail_copy(*args: object, **kwargs: object) -> None:
        raise OSError("dump executable failed")

    monkeypatch.setattr(snapshot.subprocess, "Popen", fail_copy)
    try:
        with pytest.raises(OSError, match="dump executable failed"):
            builder.clone_public("frozen-source", "smaug", "clean-target")
        assert "lock:true" in lock_commands[0]
        assert "fsyncUnlock" in lock_commands[-1]
    finally:
        builder.log.close()


def test_runtime_fingerprint_binds_schema_and_taxonomy_but_ignores_cache(
    tmp_path: Path,
) -> None:
    (tmp_path / "src").mkdir()
    (tmp_path / "alembic").mkdir()
    for filename in (
        "pyproject.toml",
        "uv.lock",
        "src/a.py",
        "src/taxonomy.json",
        "alembic/schema.py",
    ):
        (tmp_path / filename).write_text("original")
    baseline = snapshot.code_digest(tmp_path)
    (tmp_path / "src" / "a.pyc").write_bytes(b"generated cache")
    assert snapshot.code_digest(tmp_path) == baseline
    (tmp_path / "src" / "taxonomy.json").write_text("changed classification")
    assert snapshot.code_digest(tmp_path) != baseline
    (tmp_path / "src" / "taxonomy.json").write_text("original")
    (tmp_path / "alembic" / "schema.py").write_text("changed schema")
    assert snapshot.code_digest(tmp_path) != baseline


def test_native_budget_must_be_strictly_below_channel_limit(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    expected = make_snapshot(tmp_path)
    manifest = snapshot.verify_payloads(tmp_path, expected)
    monkeypatch.setattr(
        snapshot, "RELEASE_ASSET_LIMIT", manifest.native_size_budget_bytes
    )
    with pytest.raises(ValueError, match="exceeds"):
        snapshot.verify_payloads(tmp_path, expected)


@pytest.mark.parametrize("unverified", [False, None, "true", 1])
def test_publication_refuses_incomplete_or_untyped_validation(
    tmp_path: Path,
    unverified: object,
) -> None:
    expected = make_snapshot(tmp_path)
    manifest = snapshot.verify_payloads(tmp_path, expected)
    manifest.validation.update(
        restore_verified=unverified,
        restored_offline_recomputation_equivalent=True,
        doctor_exit_status=0,
        restored_offline_doctor_exit_status=0,
    )
    with pytest.raises(ValueError, match="not completed"):
        snapshot.require_validated(manifest)


def test_public_validation_excludes_host_configuration_and_local_paths() -> None:
    report = {
        "as_of": "2026-11-13T23:00:00+00:00",
        "scope": "complete_traded_code_universe",
        "exit_status": 0,
        "contract_sha256": "a" * 64,
        "domain_sha256": "b" * 64,
        "coverage": {"ticker_analysis": 6027},
        "excluded_fields": ["run_id"],
        "verification_failures": [],
        "platform": "private host",
        "cpu_count": 12,
        "local_path": "/home/private/workspace",
        "elapsed_seconds": 260.0,
    }
    public = snapshot.public_validation(report)
    assert "platform" not in public
    assert "cpu_count" not in public
    assert "local_path" not in public
    assert public["contract_sha256"] == report["contract_sha256"]
    assert public["domain_sha256"] == report["domain_sha256"]
    assert public["coverage"] == report["coverage"]


def test_xz_archive_preserves_logical_bytes_and_checks_integrity(
    tmp_path: Path,
) -> None:
    path = tmp_path / "mongo.archive.xz"
    logical = b"public CVM/B3 facts with exact decimals and provenance\n" * 10000
    snapshot.compress_archive(io.BytesIO(logical), path)
    output = io.BytesIO()
    snapshot.extract_archive(path, output)
    assert output.getvalue() == logical
    corrupted = bytearray(path.read_bytes())
    corrupted[len(corrupted) // 2] ^= 1
    path.write_bytes(corrupted)
    with pytest.raises(lzma.LZMAError, match="Corrupt"):
        snapshot.extract_archive(path, io.BytesIO())


def test_compression_never_requests_the_complete_archive_in_memory(
    tmp_path: Path,
) -> None:
    class BoundedSource(io.BytesIO):
        def read(self, size: int = -1) -> bytes:
            assert 0 < size <= 1024 * 1024
            return super().read(size)

    snapshot.compress_archive(
        BoundedSource(b"facts" * 1000000), tmp_path / "archive.xz"
    )


def test_disk_sampling_retries_a_disappearing_temporary_relation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    builder = make_builder(tmp_path)
    attempts = 0

    def walk(command: list[str]) -> str:
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise snapshot.subprocess.CalledProcessError(1, command)
        return "1234 /data/db"

    monkeypatch.setattr(builder, "run", walk)
    try:
        assert builder.disk_bytes("database", "/data/db") == 1234
        assert attempts == 2
    finally:
        builder.log.close()


def test_persistent_disk_sampling_failure_is_not_hidden(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    builder = make_builder(tmp_path)
    attempts = 0

    def walk(command: list[str]) -> str:
        nonlocal attempts
        attempts += 1
        raise snapshot.subprocess.CalledProcessError(1, command)

    monkeypatch.setattr(builder, "run", walk)
    try:
        with pytest.raises(snapshot.subprocess.CalledProcessError):
            builder.disk_bytes("database", "/data/db")
        assert attempts == 3
    finally:
        builder.log.close()


def test_snapshot_directory_cannot_smuggle_other_data(tmp_path: Path) -> None:
    expected = make_snapshot(tmp_path)
    (tmp_path / "personal-portfolio.csv").write_text("private user data")
    with pytest.raises(ValueError, match="Unexpected files"):
        snapshot.verify_payloads(tmp_path, expected)
