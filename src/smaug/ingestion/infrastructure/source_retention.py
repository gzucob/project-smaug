"""Release installation source files only after all consumers persist successfully."""

from __future__ import annotations

import asyncio
import hashlib
import json
import shutil
from collections.abc import Mapping
from datetime import UTC, datetime
from pathlib import Path
from types import TracebackType
from typing import Any, BinaryIO

from pymongo.asynchronous.collection import AsyncCollection
from pymongo.asynchronous.database import AsyncDatabase
from pymongo.write_concern import WriteConcern

from smaug.ingestion.application.source_inputs import MirrorSourceInputs
from smaug.ingestion.infrastructure.source_inputs import (
    LocalSourceInputReader,
    MongoSourceInputRepository,
)
from smaug.shared.artifacts import SourceArtifact
from smaug.shared.config import Settings
from smaug.shared.errors import SourceError


class MongoArtifactObservations:
    """Persist acquisition identity and validators before source-file cleanup."""

    def __init__(self, collection: AsyncCollection[dict[str, Any]]) -> None:
        # Mongo carries source-specific observation fields without interpretation.
        self._collection = collection

    async def latest(self, source_url: str) -> Mapping[str, object]:
        row = await self._collection.find_one(
            {"metadata.source_url": source_url, "kind": "acquisition"},
            sort=[("observed_at", -1), ("observation_key", -1)],
        )
        return dict(row["metadata"]) if row is not None else {}

    async def record(self, observation: Mapping[str, object]) -> None:
        encoded = json.dumps(dict(observation), sort_keys=True, separators=(",", ":"))
        key = hashlib.sha256(encoded.encode()).hexdigest()
        timestamp = observation.get("observed_at")
        observed = (
            datetime.fromisoformat(str(timestamp)) if timestamp else datetime.now(UTC)
        )
        await self._collection.update_one(
            {"observation_key": key},
            {
                "$setOnInsert": {
                    "observation_key": key,
                    "observed_at": observed,
                    "kind": "acquisition",
                    "metadata": dict(observation),
                }
            },
            upsert=True,
        )


class InstallationSourceSession:
    """One resumable operation owns a private workspace and an exclusive file lock."""

    def __init__(
        self,
        settings: Settings,
        database: AsyncDatabase[dict[str, Any]],
        *,
        scope: str,
        operation: str,
        resume_run_id: str | None = None,
    ) -> None:
        if settings.runtime_context != "installation":
            raise ValueError("source cleanup requires installation deployment identity")
        if len(scope) != 64 or any(c not in "0123456789abcdef" for c in scope):
            raise ValueError("invalid source workspace scope")
        self._base = Path(settings.source_artifact_dir).resolve()
        self.workspace = self._base / ".ingestion" / scope
        self.settings = settings.model_copy(
            update={
                "source_artifact_dir": str(self.workspace / "sources"),
                "cvm_cache_dir": str(self.workspace / "cvm"),
                "b3_cache_dir": str(self.workspace / "b3"),
            }
        )
        self._original_settings = settings
        self._resume_run_id = resume_run_id
        self._scope = scope
        self._operation = operation
        durable = WriteConcern(w="majority", j=True)
        self._sessions = database["source_file_sessions"].with_options(
            write_concern=durable
        )
        self._acquisitions = database["source_acquisitions"].with_options(
            write_concern=durable
        )
        self.observations = MongoArtifactObservations(self._acquisitions)
        self._mirror = MirrorSourceInputs(
            MongoSourceInputRepository(
                database["source_input_archives"].with_options(write_concern=durable),
                database["source_input_rows"].with_options(write_concern=durable),
            )
        )
        self._lock: BinaryIO | None = None
        self._complete = False

    def _acquire_lock(self) -> None:
        # Native installations run this Python process in a Linux Docker
        # container on every host platform. Locks are released by the kernel
        # after interruption, including SIGKILL; no expiring lease can race I/O.
        import fcntl

        self._base.mkdir(parents=True, exist_ok=True)
        handle = (self._base / ".source-retention.lock").open("a+b")
        try:
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            handle.close()
            raise SourceError("source_ingestion_already_running") from exc
        self._lock = handle

    def _unlock(self) -> None:
        if self._lock is not None:
            self._lock.close()
            self._lock = None

    async def __aenter__(self) -> InstallationSourceSession:
        self._acquire_lock()
        try:
            if self._resume_run_id is not None:
                owner = await self._sessions.find_one(
                    {"ingestion_run_ids": self._resume_run_id}
                )
                if owner is not None and owner["state"] != "cleaned":
                    recovered_scope = owner["scope"]
                    if len(recovered_scope) != 64 or any(
                        c not in "0123456789abcdef" for c in recovered_scope
                    ):
                        raise SourceError("unsafe_source_workspace_scope")
                    self._scope = recovered_scope
                    self.workspace = self._base / ".ingestion" / self._scope
                    self.settings = self._original_settings.model_copy(
                        update={
                            "source_artifact_dir": str(self.workspace / "sources"),
                            "cvm_cache_dir": str(self.workspace / "cvm"),
                            "b3_cache_dir": str(self.workspace / "b3"),
                        }
                    )
            previous = await self._sessions.find_one({"scope": self._scope})
            if previous is not None and previous["state"] == "consumers_complete":
                await self._remove_workspace()
                await self._mark_cleaned()
            if self.workspace.is_symlink() or self.workspace.parent.is_symlink():
                raise SourceError("unsafe_source_workspace")
            self.workspace.mkdir(parents=True, exist_ok=True)
            await self._sessions.update_one(
                {"scope": self._scope},
                {
                    "$set": {
                        "operation": self._operation,
                        "state": "active",
                        "started_at": datetime.now(UTC),
                        "error": None,
                    },
                    "$inc": {"attempts": 1},
                },
                upsert=True,
            )
            return self
        except BaseException:
            self._unlock()
            raise

    async def observe_artifact(self, artifact: SourceArtifact) -> None:
        observed = datetime.now(UTC)
        await self.observations.record(
            {
                "artifact_id": artifact.artifact_id,
                "sha256": artifact.sha256,
                "byte_size": artifact.byte_size,
                "source_url": artifact.source_url,
                "observed_at": observed.isoformat(),
                "downloaded_at": (artifact.downloaded_at or observed).isoformat(),
                "etag": artifact.etag,
                "last_modified": artifact.last_modified,
                "http_status": 200,
            }
        )
        await self._mirror.execute(
            LocalSourceInputReader(artifact.path, artifact=artifact)
        )

    async def record_run(self, run_id: str) -> None:
        await self._sessions.update_one(
            {"scope": self._scope}, {"$addToSet": {"ingestion_run_ids": run_id}}
        )

    async def _flush_metadata(self) -> None:
        sources = Path(self.settings.source_artifact_dir)
        for path in sorted((sources / "observations").glob("*.json")):
            observation = json.loads(await asyncio.to_thread(path.read_text))
            await self.observations.record(observation)
        for path in sorted((sources / "quarantine" / "manifests").glob("*/*.json")):
            payload = json.loads(await asyncio.to_thread(path.read_text))
            key = "quarantine:" + self._scope + ":" + path.stem
            await self._acquisitions.update_one(
                {"observation_key": key},
                {
                    "$setOnInsert": {
                        "kind": "quarantine",
                        "observed_at": datetime.now(UTC),
                        "metadata": payload,
                    }
                },
                upsert=True,
            )

    async def complete(self) -> None:
        """Reconcile every source copy, checkpoint consumers, then remove files."""
        await self._flush_metadata()
        admitted = set()
        sources = Path(self.settings.source_artifact_dir)
        for path in sorted(self.workspace.rglob("*")):
            if path.suffix.lower() != ".zip" or ".staging" in path.parts:
                continue
            if "quarantine" in path.parts:
                raise SourceError("quarantined_source_prevents_cleanup")
            artifact = None
            # Preserve the original HTTP identity when materializing a retained
            # blob; standalone legacy cache copies still receive raw admission.
            if path.parent.parent == sources / "sha256":
                digest = path.stem
                state = await self._acquisitions.find_one(
                    {"metadata.artifact_id": "sha256:" + digest, "kind": "acquisition"},
                    sort=[("observed_at", -1)],
                )
                if state is not None:
                    meta = state["metadata"]
                    artifact = SourceArtifact(
                        "sha256:" + digest,
                        digest,
                        path.stat().st_size,
                        path,
                        str(meta["source_url"]),
                        datetime.fromisoformat(meta["downloaded_at"]),
                        meta.get("etag"),
                        meta.get("last_modified"),
                    )
            reader = LocalSourceInputReader(path, artifact=artifact)
            # Admission is idempotent and verifies the same bytes the existing
            # ingestion readers consumed. An interrupted write never admits rows.
            await self._mirror.execute(reader)
            admitted.add(reader.archive.artifact_id)
        await self._sessions.update_one(
            {"scope": self._scope},
            {
                "$set": {
                    "state": "consumers_complete",
                    "completed_at": datetime.now(UTC),
                },
                "$addToSet": {"artifact_ids": {"$each": sorted(admitted)}},
            },
        )
        await self._remove_workspace()
        await self._mark_cleaned()
        self._complete = True

    async def _remove_workspace(self) -> None:
        if self.workspace.is_symlink() or self.workspace.parent.is_symlink():
            raise SourceError("unsafe_source_workspace")
        if self.workspace.exists():
            removal = asyncio.create_task(
                asyncio.to_thread(shutil.rmtree, self.workspace)
            )
            cancelled = False
            while True:
                try:
                    await asyncio.shield(removal)
                    break
                except asyncio.CancelledError:
                    # Keep ownership through repeated cancellation until the
                    # filesystem worker can no longer remove a resumed path.
                    if removal.cancelled():
                        raise
                    cancelled = True
            if cancelled:
                raise asyncio.CancelledError

    async def _mark_cleaned(self) -> None:
        await self._sessions.update_one(
            {"scope": self._scope},
            {"$set": {"state": "cleaned", "cleaned_at": datetime.now(UTC)}},
        )

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        try:
            if not self._complete:
                # If cleanup itself was interrupted, keep the durable consumer
                # checkpoint so a retry finishes deletion rather than reingesting.
                row = await self._sessions.find_one({"scope": self._scope})
                if row is not None and row["state"] != "consumers_complete":
                    await self._flush_metadata()
                    await self._sessions.update_one(
                        {"scope": self._scope},
                        {
                            "$set": {
                                "state": "failed",
                                "error": type(exc).__name__
                                if exc
                                else "consumers_incomplete",
                            }
                        },
                    )
        finally:
            self._unlock()
