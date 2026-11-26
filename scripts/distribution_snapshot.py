"""Build and verify public logical snapshots in disposable, offline Docker databases.

Requires Docker, the repository's uv environment and a frozen CVM/B3 Mongo mirror.
Never reads a source PostgreSQL database or copies a personal database wholesale.
Only the explicitly named source Mongo container is briefly write-locked; all
other mutations affect containers created by this process. Dumps stay outside Git.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import lzma
import os
import re
import shutil
import subprocess
import sys
import threading
import time
import uuid
from collections.abc import Iterator
from contextlib import ExitStack, contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import IO, Any

from alembic.config import Config
from alembic.script import ScriptDirectory
from pydantic import BaseModel, ConfigDict, Field

from smaug.analysis.domain.indicators import CALCULATION_CONTRACT_VERSION

ROOT = Path(__file__).resolve().parents[1]
# Required by the Mongo-only readers, including durable acceptance decisions.
MONGO_COLLECTIONS = (
    "raw_ingestions",
    "ingestion_validations",
    "source_input_archives",
    "source_input_rows",
)
SQL_TABLES = ("ticker_analysis", "analysis_outcomes", "price_histories")
EMPTY_TABLES = (
    "portfolio",
    "price_refresh_status",
    "ui_data_revision",
    "refresh_checkpoint",
)
PAYLOAD_NAMES = ("mongo.archive.xz", "postgres.dump")
RELEASE_ASSET_LIMIT = 2 * 1024**3
# Conservative allowance for launcher, application metadata and native wrapping.
NATIVE_OVERHEAD_BYTES = 128 * 1024**2


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class Payload(StrictModel):
    name: str
    size_bytes: int = Field(gt=0)
    sha256: str = Field(pattern=r"^[a-f0-9]{64}$")


class Compatibility(StrictModel):
    code_commit: str = Field(pattern=r"^[a-f0-9]{40}$")
    code_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    alembic_revision: str
    calculation_contract: str
    source_parser: str = "source-inputs-v1"
    mongo_major: int = 8
    postgres_major: int = 17


class Manifest(StrictModel):
    format_version: int
    created_at: str
    compatibility: Compatibility
    data_revision: str = Field(pattern=r"^[a-f0-9]{64}$")
    # Source metadata and validation evidence are versioned external documents.
    source_archives: list[dict[str, Any]]
    source_dates: dict[str, str | None]
    coverage: dict[str, int]
    mongo_hashes: dict[str, str]
    sql_hashes: dict[str, str]
    # Phase-4 reports contain heterogeneous timing and coverage metadata.
    validation: dict[str, Any]
    payloads: list[Payload]
    native_size_budget_bytes: int


def digest_file(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def compress_archive(source: IO[bytes], destination: Path) -> None:
    """Compress a logical archive with bounded buffers and a 1 MiB dictionary."""
    with lzma.open(destination, "wb", preset=1, check=lzma.CHECK_CRC64) as output:
        shutil.copyfileobj(source, output, length=1024 * 1024)


def extract_archive(source: Path, destination: IO[bytes]) -> None:
    """Decode XZ as a stream; no extracted archive is retained on disk."""
    with lzma.open(source, "rb") as archive:
        shutil.copyfileobj(archive, destination, length=1024 * 1024)


def code_digest(root: Path = ROOT) -> str:
    """Bind runtime code, taxonomy, schema and locked dependencies to the snapshot."""
    digest = hashlib.sha256()
    paths = [root / "pyproject.toml", root / "uv.lock"]
    paths += sorted((root / "src").rglob("*.py"))
    paths += sorted((root / "src").rglob("*.json"))
    paths += sorted((root / "alembic").rglob("*.py"))
    for path in sorted(paths):
        digest.update(path.relative_to(root).as_posix().encode() + b"\0")
        digest.update(path.read_bytes())
    return digest.hexdigest()


def compatibility() -> Compatibility:
    revision = ScriptDirectory.from_config(Config(str(ROOT / "alembic.ini")))
    return Compatibility(
        code_commit=subprocess.check_output(
            ["git", "rev-parse", "HEAD"], cwd=ROOT, text=True
        ).strip(),
        code_sha256=code_digest(),
        alembic_revision=str(revision.get_current_head()),
        calculation_contract=CALCULATION_CONTRACT_VERSION,
    )


def verify_payloads(directory: Path, expected: Compatibility) -> Manifest:
    """Reject incompatibility and corruption before opening a restore target."""
    if {path.name for path in directory.iterdir()} != {"manifest.json", *PAYLOAD_NAMES}:
        raise ValueError("Unexpected files in snapshot directory")
    manifest = Manifest.model_validate_json((directory / "manifest.json").read_bytes())
    if manifest.format_version != 1:
        raise ValueError("Unsupported snapshot format")
    # A squash/release commit may change the Git ID without changing runtime code.
    actual = manifest.compatibility.model_dump(exclude={"code_commit"})
    if actual != expected.model_dump(exclude={"code_commit"}):
        raise ValueError("Snapshot is incompatible with this runtime/schema/contract")
    if [payload.name for payload in manifest.payloads] != list(PAYLOAD_NAMES):
        raise ValueError("Unexpected snapshot payload names")
    total = 0
    for payload in manifest.payloads:
        path = directory / payload.name
        if path.is_symlink() or not path.is_file():
            raise ValueError(f"Invalid payload: {payload.name}")
        if path.stat().st_size != payload.size_bytes:
            raise ValueError(f"Payload size mismatch: {payload.name}")
        if digest_file(path) != payload.sha256:
            raise ValueError(f"Payload checksum mismatch: {payload.name}")
        total += payload.size_bytes
    budget = total + NATIVE_OVERHEAD_BYTES
    if manifest.native_size_budget_bytes != budget or budget >= RELEASE_ASSET_LIMIT:
        raise ValueError("Native package exceeds the GitHub Releases asset budget")
    revision = hashlib.sha256(
        json.dumps(
            [manifest.mongo_hashes, manifest.sql_hashes], sort_keys=True
        ).encode()
    ).hexdigest()
    if manifest.data_revision != revision:
        raise ValueError("Data revision mismatch")
    return manifest


def require_validated(manifest: Manifest) -> None:
    for key in (
        "restore_verified",
        "restored_offline_recomputation_equivalent",
    ):
        if manifest.validation.get(key) is not True:
            raise ValueError("Snapshot has not completed restore verification")
    for key in ("doctor_exit_status", "restored_offline_doctor_exit_status"):
        if manifest.validation.get(key) != 0:
            raise ValueError("Snapshot has not passed the offline diagnostic")


def public_validation(report: dict[str, Any]) -> dict[str, Any]:
    """Keep calculation evidence in the payload and host measurements in reports."""
    return {
        key: report[key]
        for key in (
            "as_of",
            "scope",
            "exit_status",
            "contract_sha256",
            "domain_sha256",
            "coverage",
            "excluded_fields",
            "verification_failures",
        )
    }


class Builder:
    def __init__(self, work: Path) -> None:
        self.work = work
        self.prefix = "smaug-snapshot-" + uuid.uuid4().hex[:10]
        self.network = self.prefix
        self.containers: list[str] = []
        self.log = (work / "build.log").open("ab", buffering=0)

    def run(self, command: list[str], *, env: dict[str, str] | None = None) -> str:
        result = subprocess.run(
            command,
            cwd=ROOT,
            env=env,
            stdout=subprocess.PIPE,
            stderr=self.log,
            check=True,
            text=True,
        )
        return result.stdout.strip()

    def stream(self, command: list[str], path: Path, *, restore: bool = False) -> None:
        with path.open("rb" if restore else "wb") as stream:
            subprocess.run(
                command,
                cwd=ROOT,
                check=True,
                stderr=self.log,
                stdin=stream if restore else subprocess.DEVNULL,
                stdout=self.log if restore else stream,
            )

    # mongosh returns untyped BSON-derived source and server metadata.
    def mongo(self, container: str, javascript: str, database: str = "smaug") -> Any:
        # mongosh JSON serialization is used only for small metadata, never dumps.
        output = self.run(
            [
                "docker",
                "exec",
                container,
                "mongosh",
                "--quiet",
                database,
                "--eval",
                f"JSON.stringify((()=>{{{javascript}}})())",
            ]
        )
        return json.loads(output)

    def sql(self, container: str, statement: str) -> str:
        return self.run(
            [
                "docker",
                "exec",
                container,
                "psql",
                "-X",
                "-v",
                "ON_ERROR_STOP=1",
                "-U",
                "smaug",
                "-d",
                "smaug",
                "-Atc",
                statement,
            ]
        )

    @contextmanager
    def resources(self) -> Iterator[None]:
        self.run(["docker", "network", "create", "--internal", self.network])
        try:
            yield
        finally:
            for name in reversed(self.containers):
                self.run(["docker", "rm", "-f", "-v", name])
            self.run(["docker", "network", "rm", self.network])
            self.log.close()

    def retire(self, *names: str) -> None:
        for name in names:
            self.run(["docker", "rm", "-f", "-v", name])
            self.containers.remove(name)

    def disk_bytes(self, container: str, path: str) -> int:
        # PostgreSQL renames/removes relation files during restore. Retry a
        # racing directory walk; persistent sampling failures still fail the gate.
        for attempt in range(3):
            try:
                return int(
                    self.run(
                        [
                            "docker",
                            "exec",
                            container,
                            "du",
                            "-s",
                            "-B1",
                            path,
                        ]
                    ).split()[0]
                )
            except subprocess.CalledProcessError:
                if attempt == 2:
                    raise
                time.sleep(0.05)
        raise AssertionError("unreachable disk sample")

    def databases(self, suffix: str) -> tuple[str, str, dict[str, str]]:
        mongo, postgres = f"{self.prefix}-{suffix}-mongo", f"{self.prefix}-{suffix}-pg"
        for name, image, extra in (
            (mongo, "mongo:8", []),
            (
                postgres,
                "postgres:17",
                [
                    "-e",
                    "POSTGRES_USER=smaug",
                    "-e",
                    "POSTGRES_PASSWORD=smaug",
                    "-e",
                    "POSTGRES_DB=smaug",
                ],
            ),
        ):
            self.run(
                [
                    "docker",
                    "run",
                    "-d",
                    "--name",
                    name,
                    "--network",
                    self.network,
                    *extra,
                    image,
                    *(["--wiredTigerCacheSizeGB", "2"] if name == mongo else []),
                ]
            )
            self.containers.append(name)
        for name, check in (
            (mongo, ["mongosh", "--quiet", "--eval", "db.adminCommand({ping:1})"]),
            (postgres, ["pg_isready", "-U", "smaug"]),
        ):
            for _ in range(60):
                try:
                    self.run(["docker", "exec", name, *check])
                    break
                except subprocess.CalledProcessError:
                    time.sleep(1)
            else:
                raise RuntimeError(f"Database not ready: {name}")
        addresses = [
            self.run(
                [
                    "docker",
                    "inspect",
                    "--format",
                    "{{range .NetworkSettings.Networks}}{{.IPAddress}}{{end}}",
                    name,
                ]
            )
            for name in (mongo, postgres)
        ]
        # Explicit settings prevent a local .env from redirecting a build or
        # introducing machine-dependent cache paths, modules or FCA selection.
        env = {
            key: value
            for key, value in os.environ.items()
            if not key.startswith(("MONGO_", "POSTGRES_", "CVM_", "B3_", "SMAUG_"))
        }
        env.update(
            MONGO_URI=f"mongodb://{addresses[0]}:27017",
            MONGO_DB="smaug",
            POSTGRES_URI=f"postgresql+asyncpg://smaug:smaug@{addresses[1]}:5432/smaug",
            AUTO_PRICE_REFRESH="false",
            SMAUG_RUNTIME_CONTEXT="development",
            CVM_FCA_YEAR="2026",
            CVM_CACHE_DIR=str(self.work / "absent-cvm"),
            B3_CACHE_DIR=str(self.work / "absent-b3"),
        )
        return mongo, postgres, env

    def clone_public(self, source: str, database: str, target: str) -> None:
        # fsyncLock blocks every writer, including ones outside Smaug. It is
        # always released, and never used on the user's PostgreSQL database.
        self.mongo(source, "return db.adminCommand({fsync:1,lock:true});", database)
        try:
            for collection in MONGO_COLLECTIONS:
                command = [
                    "docker",
                    "exec",
                    source,
                    "mongodump",
                    f"--db={database}",
                    f"--collection={collection}",
                    "--archive",
                    "--gzip",
                ]
                with subprocess.Popen(
                    command, stdout=subprocess.PIPE, stderr=self.log
                ) as dump:
                    try:
                        subprocess.run(
                            [
                                "docker",
                                "exec",
                                "-i",
                                target,
                                "mongorestore",
                                "--archive",
                                "--gzip",
                                "--stopOnError",
                                f"--nsFrom={database}.*",
                                "--nsTo=smaug.*",
                            ],
                            stdin=dump.stdout,
                            stdout=self.log,
                            stderr=self.log,
                            check=True,
                        )
                    finally:
                        if dump.stdout is not None:
                            dump.stdout.close()
                    if dump.wait() != 0:
                        raise RuntimeError(f"Failed to copy {collection}")
        finally:
            self.mongo(source, "return db.adminCommand({fsyncUnlock:1});", database)
        self.sanitize(target)

    def sanitize(self, mongo: str) -> None:
        # Raw payloads and lossless rows are public CVM/B3 facts, never rewritten.
        # Free-form operator notes and machine-path metadata are not source facts.
        self.mongo(
            mongo,
            """
            db.ingestion_validations.updateMany({}, {$set:{approval_note:null}});
            for (const name of ['raw_ingestions','ingestion_validations']) {
                const ops=[];
                function clean(value) {
                    if (typeof value === 'string' &&
                        /^(\\/|[A-Za-z]:[\\\\/]|file:|\\\\\\\\)/.test(value))
                        return null;
                    if (Array.isArray(value)) return value.map(clean);
                    if (value && typeof value === 'object' &&
                        !(value instanceof Date) && !(value instanceof ObjectId)) {
                        const out={};
                        for (const [key,item] of Object.entries(value)) {
                            if (/path|directory|cache_dir|hostname|username/i.test(key))
                                continue;
                            if (typeof item === 'string' &&
                                /^(\\/|[A-Za-z]:[\\\\/]|file:|\\\\\\\\)/.test(item))
                                continue;
                            out[key]=clean(item);
                        }
                        return out;
                    }
                    return value;
                }
                const fields = name === 'raw_ingestions' ? ['request'] :
                    ['evidence','observations','findings'];
                db[name].find().forEach(doc => {
                    const set={};
                    for (const field of fields) set[field]=clean(doc[field]);
                    ops.push({updateOne:{filter:{_id:doc._id},update:{$set:set}}});
                    if (ops.length === 500) {db[name].bulkWrite(ops);ops.length=0;}
                });
                if (ops.length) db[name].bulkWrite(ops);
            }
            return true;
        """,
        )

    # Server metadata combines counts, BSON hashes and external source documents.
    def inspect(self, mongo: str, postgres: str) -> dict[str, Any]:
        metadata = self.mongo(
            mongo,
            r"""
            const names=['raw_ingestions','ingestion_validations',
                         'source_input_archives','source_input_rows'];
            const counts=Object.fromEntries(names.map(n=>[n,db[n].countDocuments()]));
            if (names.some(n=>!counts[n])) throw Error('Empty public collection');
            if (db.raw_ingestions.countDocuments({source:{$nin:['cvm','b3']}}))
                throw Error('Non-public source');
            const hashes=db.runCommand({dbHash:1,collections:names});
            if (!hashes.ok) throw Error('dbHash failed');
            const archives=db.source_input_archives.find({}, {_id:0}).sort(
                {dataset:1,year:1,artifact_id:1}).toArray();
            let expected=0;
            for (const a of archives) {
                if (a.parser_version !== 'source-inputs-v1' ||
                    a.validation !== 'zip-crc-and-lossless-row-layout-v1' ||
                    !/^https:\/\/(dados.cvm.gov.br|bvmf.bmfbovespa.com.br)\//.test(
                        a.source_url)) throw Error('Unadmitted source archive');
                for (const [member,count] of Object.entries(a.member_counts)) {
                    const q={artifact_id:a.artifact_id,parser_version:a.parser_version,
                             member:member};
                    const size=db.source_input_rows.countDocuments(q);
                    const lo=db.source_input_rows.find(q).sort({ordinal:1}).limit(1)
                        .toArray()[0];
                    const hi=db.source_input_rows.find(q).sort({ordinal:-1}).limit(1)
                        .toArray()[0];
                    if (size !== count || (count &&
                        (lo.ordinal !== 1 || hi.ordinal !== count)))
                        throw Error('Incomplete source member');
                    expected+=count;
                }
            }
            if (expected !== counts.source_input_rows)
                throw Error('Orphan source rows');
            return {coverage:counts,mongo_hashes:hashes.collections,
                    source_archives:archives, mongo_bytes:db.stats().storageSize+
                    db.stats().indexSize};
        """,
        )
        sql_hashes = {}
        for table in SQL_TABLES:
            path = self.work / f"{postgres}-{table}.jsonl"
            self.stream(
                [
                    "docker",
                    "exec",
                    postgres,
                    "psql",
                    "-X",
                    "-v",
                    "ON_ERROR_STOP=1",
                    "-U",
                    "smaug",
                    "-d",
                    "smaug",
                    "-c",
                    f"COPY (SELECT row_to_json(t) FROM {table} t "
                    "ORDER BY id) TO STDOUT",
                ],
                path,
            )
            sql_hashes[table] = digest_file(path)
            path.unlink()
            metadata["coverage"][table] = int(
                self.sql(postgres, f"SELECT count(*) FROM {table}")
            )
        for table in EMPTY_TABLES:
            if int(self.sql(postgres, f"SELECT count(*) FROM {table}")):
                raise ValueError(f"Private/operational state in {table}")
        if int(
            self.sql(
                postgres,
                "SELECT count(*) FROM analysis_outcomes WHERE status = 'error'",
            )
        ):
            raise ValueError("Failed analysis outcomes")
        contracts = self.sql(
            postgres,
            "SELECT DISTINCT calculation_contract_version FROM ticker_analysis",
        )
        if contracts != CALCULATION_CONTRACT_VERSION:
            raise ValueError("Unvalidated calculation contract")
        metadata["sql_hashes"] = sql_hashes
        metadata["postgres_bytes"] = int(
            self.sql(postgres, "SELECT pg_database_size(current_database())")
        )
        metadata["source_dates"] = json.loads(
            self.sql(
                postgres,
                """
            SELECT json_build_object(
                'first_analysis_period', min(reference_date),
                'last_analysis_period', max(reference_date),
                'last_selected_b3_session', max(price_source_session))
            FROM ticker_analysis
        """,
            )
        )
        metadata["disk_bytes"] = {
            "mongo": self.disk_bytes(mongo, "/data/db"),
            "postgres": self.disk_bytes(postgres, "/var/lib/postgresql/data"),
        }
        metadata["data_revision"] = hashlib.sha256(
            json.dumps([metadata["mongo_hashes"], sql_hashes], sort_keys=True).encode()
        ).hexdigest()
        return dict(metadata)

    def command(self, args: list[str], env: dict[str, str], log: str) -> None:
        with (self.work / log).open("wb") as stream:
            subprocess.run(
                [sys.executable, *args],
                cwd=ROOT,
                env=env,
                stdout=stream,
                stderr=subprocess.STDOUT,
                check=True,
            )

    def offline_checks(self, env: dict[str, str], log: str) -> None:
        self.command([str(Path(__file__)), "check-offline"], env, log)

    def export(self, mongo: str, postgres: str, output: Path) -> None:
        # The isolated build has no app services. Make both DBs reject writes
        # as well, spanning the entire pair of logical exports.
        self.sql(postgres, "ALTER DATABASE smaug SET default_transaction_read_only=on")
        self.mongo(mongo, "return db.adminCommand({fsync:1,lock:true});")
        try:
            with subprocess.Popen(
                ["docker", "exec", mongo, "mongodump", "--db=smaug", "--archive"],
                stdout=subprocess.PIPE,
                stderr=self.log,
            ) as dump:
                assert dump.stdout is not None
                try:
                    compress_archive(dump.stdout, output / PAYLOAD_NAMES[0])
                finally:
                    dump.stdout.close()
                if dump.wait() != 0:
                    raise RuntimeError("Mongo logical export failed")
            self.stream(
                [
                    "docker",
                    "exec",
                    postgres,
                    "pg_dump",
                    "-U",
                    "smaug",
                    "-d",
                    "smaug",
                    "--format=custom",
                    "--compress=9",
                    "--no-owner",
                    "--no-acl",
                ],
                output / PAYLOAD_NAMES[1],
            )
        finally:
            self.mongo(mongo, "return db.adminCommand({fsyncUnlock:1});")

    def restore(self, mongo: str, postgres: str, directory: Path) -> dict[str, Any]:
        verify_payloads(directory, compatibility())
        # Fresh containers only: never --drop, --clean or a production URI.
        if self.mongo(mongo, "return db.getCollectionNames();"):
            raise ValueError("Mongo restore target is not empty")
        if self.sql(
            postgres, "SELECT tablename FROM pg_tables WHERE schemaname='public'"
        ):
            raise ValueError("PostgreSQL restore target is not empty")
        started = time.monotonic()
        peak = {"mongo": 0, "postgres": 0}
        stopped = threading.Event()
        errors: list[str] = []

        def sample() -> None:
            while not stopped.is_set():
                try:
                    for name, container, path in (
                        ("mongo", mongo, "/data/db"),
                        ("postgres", postgres, "/var/lib/postgresql/data"),
                    ):
                        peak[name] = max(peak[name], self.disk_bytes(container, path))
                except (OSError, subprocess.SubprocessError) as exc:
                    errors.append(str(exc))
                    return
                stopped.wait(1)

        sampler = threading.Thread(target=sample, daemon=True)
        sampler.start()
        try:
            self._import(mongo, postgres, directory)
        finally:
            seconds = time.monotonic() - started
            stopped.set()
            sampler.join()
        if errors:
            raise RuntimeError(f"Disk sampling failed: {errors}")
        for name, container, path in (
            ("mongo", mongo, "/data/db"),
            ("postgres", postgres, "/var/lib/postgresql/data"),
        ):
            peak[name] = max(peak[name], self.disk_bytes(container, path))
        return {
            "seconds": seconds,
            "peak_disk_bytes": peak,
            "disk_sample_interval_seconds": 1,
        }

    def _import(self, mongo: str, postgres: str, directory: Path) -> None:
        with subprocess.Popen(
            [
                "docker",
                "exec",
                "-i",
                mongo,
                "mongorestore",
                "--archive",
                "--stopOnError",
            ],
            stdin=subprocess.PIPE,
            stdout=self.log,
            stderr=self.log,
        ) as restore:
            assert restore.stdin is not None
            try:
                extract_archive(directory / PAYLOAD_NAMES[0], restore.stdin)
            finally:
                restore.stdin.close()
            if restore.wait() != 0:
                raise RuntimeError("Mongo logical restore failed")
        self.stream(
            [
                "docker",
                "exec",
                "-i",
                postgres,
                "pg_restore",
                "-U",
                "smaug",
                "-d",
                "smaug",
                "--exit-on-error",
                "--no-owner",
                "--no-acl",
            ],
            directory / PAYLOAD_NAMES[1],
            restore=True,
        )


def check_offline(*, histories: bool = False) -> None:
    # Reuse the phase-4 boundary guard. No financial files or external network
    # are available to doctor, identity selection or the read repositories.
    from profile_analysis import deny_sources

    from smaug.entrypoints import cli

    with ExitStack() as stack:
        deny_sources(stack)
        if histories:
            status = asyncio.run(
                cli._run_analyze(
                    (),
                    whole_exchange=True,
                    price_history_since=2010,
                )
            )
        else:
            status = asyncio.run(cli._run_doctor((), whole_exchange=True))
        if status:
            raise SystemExit(status)


def build(args: argparse.Namespace) -> None:
    as_of = datetime.fromisoformat(args.as_of)
    if as_of.tzinfo is None:
        raise ValueError("--as-of must include a timezone")
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]*", args.source_container):
        raise ValueError("Invalid source container")
    if not re.fullmatch(r"[A-Za-z0-9_.-]+", args.source_database):
        raise ValueError("Invalid source database")
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=False)
    work = output.parent / (output.name + "-validation")
    work.mkdir(exist_ok=False)
    builder = Builder(work)
    with builder.resources():
        mongo, postgres, env = builder.databases("build")
        print("Copying public source collections into clean databases", flush=True)
        builder.clone_public(args.source_container, args.source_database, mongo)
        builder.command(["-m", "alembic", "upgrade", "head"], env, "migrations.log")
        print("Computing the complete universe offline", flush=True)
        builder.command(
            [
                "scripts/profile_analysis.py",
                "--isolated",
                "--as-of",
                args.as_of,
                "--output",
                str(work / "analysis.json"),
            ],
            env,
            "analysis.log",
        )
        # Daily series are a separate CLI calculation, also using only MongoDB.
        builder.command(
            [
                str(Path(__file__)),
                "prepare-histories",
            ],
            env,
            "price-histories.log",
        )
        builder.offline_checks(env, "doctor-build.log")
        # Empty machine-specific generations; the installer initializes its own.
        builder.sql(postgres, "TRUNCATE ui_data_revision")
        for name in builder.mongo(mongo, "return db.getCollectionNames();"):
            if name not in MONGO_COLLECTIONS:
                if builder.mongo(mongo, f"return db['{name}'].countDocuments();"):
                    raise ValueError(f"Operational Mongo state: {name}")
                builder.mongo(mongo, f"return db['{name}'].drop();")
        print("Validating and exporting logical dumps", flush=True)
        before = builder.inspect(mongo, postgres)
        builder.export(mongo, postgres, output)
        analysis = json.loads((work / "analysis.json").read_text())
        if analysis["exit_status"] or analysis["verification_failures"]:
            raise ValueError("Analysis validation failed")
        payloads = [
            Payload(
                name=name,
                size_bytes=(output / name).stat().st_size,
                sha256=digest_file(output / name),
            )
            for name in PAYLOAD_NAMES
        ]
        images = {
            name: builder.run(
                [
                    "docker",
                    "inspect",
                    "--format",
                    "{{.Image}}",
                    container,
                ]
            )
            for name, container in (("mongo", mongo), ("postgres", postgres))
        }
        manifest = Manifest(
            format_version=1,
            created_at=datetime.now(UTC).isoformat(),
            compatibility=compatibility(),
            data_revision=before["data_revision"],
            source_archives=before["source_archives"],
            source_dates=before["source_dates"],
            coverage=before["coverage"],
            mongo_hashes=before["mongo_hashes"],
            sql_hashes=before["sql_hashes"],
            validation={
                "analysis": public_validation(analysis),
                "doctor_exit_status": 0,
                "restore_verified": False,
                "database_images": images,
                "build_disk_bytes": before["disk_bytes"],
                "build_storage_bytes": {
                    "mongo": before["mongo_bytes"],
                    "postgres": before["postgres_bytes"],
                },
            },
            payloads=payloads,
            native_size_budget_bytes=sum(p.size_bytes for p in payloads)
            + NATIVE_OVERHEAD_BYTES,
        )
        manifest_path = output / "manifest.json"
        manifest_path.write_text(manifest.model_dump_json(indent=2) + "\n")
        verify_payloads(output, compatibility())
        builder.retire(mongo, postgres)
        complete_validation(builder, output, manifest)


def complete_validation(builder: Builder, output: Path, manifest: Manifest) -> None:
    manifest_path = output / "manifest.json"
    (builder.work / "reference.json").write_text(
        json.dumps(public_validation(manifest.validation["analysis"]), indent=2) + "\n"
    )
    restored_mongo, restored_pg, restored_env = builder.databases("restore")
    print("Restoring into empty databases and comparing all public data", flush=True)
    restored = builder.restore(restored_mongo, restored_pg, output)
    after = builder.inspect(restored_mongo, restored_pg)
    for key in ("coverage", "mongo_hashes", "sql_hashes", "data_revision"):
        if getattr(manifest, key) != after[key]:
            raise ValueError(f"Restore mismatch: {key}")
    builder.offline_checks(restored_env, "doctor-restored.log")
    # Full recomputation proves that restored results have no financial-file
    # dependency; a third empty SQL database is used by the phase-4 profiler.
    builder.sql(restored_pg, "CREATE DATABASE recomputed TEMPLATE template0")
    recompute_env = dict(restored_env)
    recompute_env["POSTGRES_URI"] = (
        restored_env["POSTGRES_URI"].rsplit("/", 1)[0] + "/recomputed"
    )
    builder.command(
        ["-m", "alembic", "upgrade", "head"],
        recompute_env,
        "recompute-migrations.log",
    )
    builder.command(
        [
            "scripts/profile_analysis.py",
            "--isolated",
            "--as-of",
            str(manifest.validation["analysis"]["as_of"]),
            "--reference",
            str(builder.work / "reference.json"),
            "--output",
            str(builder.work / "recomputed.json"),
        ],
        recompute_env,
        "recomputed.log",
    )
    manifest.validation.update(
        restore_verified=True,
        restored_offline_doctor_exit_status=0,
        restored_offline_recomputation_equivalent=True,
        import_measurement=restored,
        restored_disk_bytes=after["disk_bytes"],
        restored_storage_bytes={
            "mongo": after["mongo_bytes"],
            "postgres": after["postgres_bytes"],
        },
    )
    manifest_path.write_text(manifest.model_dump_json(indent=2) + "\n")
    verify_payloads(output, compatibility())
    print(manifest_path, flush=True)


def validate_candidate(directory: Path, work: Path) -> None:
    manifest = verify_payloads(directory, compatibility())
    if manifest.validation.get("doctor_exit_status") != 0:
        raise ValueError("Candidate lacks a successful construction diagnostic")
    work.mkdir(parents=True, exist_ok=False)
    builder = Builder(work.resolve())
    with builder.resources():
        complete_validation(builder, directory.resolve(), manifest)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    create = commands.add_parser("build")
    create.add_argument("--source-container", required=True)
    create.add_argument("--source-database", default="smaug")
    create.add_argument("--output", type=Path, required=True)
    create.add_argument("--as-of", required=True)
    verify = commands.add_parser("verify")
    verify.add_argument("directory", type=Path)
    validate = commands.add_parser("validate")
    validate.add_argument("directory", type=Path)
    validate.add_argument("--work", type=Path, required=True)
    commands.add_parser("check-offline")
    commands.add_parser("prepare-histories")
    args = parser.parse_args()
    if args.command == "build":
        build(args)
    elif args.command == "validate":
        validate_candidate(args.directory, args.work)
    elif args.command == "verify":
        result = verify_payloads(args.directory, compatibility())
        require_validated(result)
        print(result.data_revision)
    else:
        check_offline(histories=args.command == "prepare-histories")


if __name__ == "__main__":
    main()
