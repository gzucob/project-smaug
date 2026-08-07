"""Content-addressed local storage for immutable source archives."""

from __future__ import annotations

import asyncio
import hashlib
import json
import re
import zipfile
from collections.abc import Callable, Mapping
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

import httpx

from smaug.shared.artifacts import ArtifactObserver, SourceArtifact
from smaug.shared.download import DownloadResult, Sleeper, download_zip
from smaug.shared.errors import CvmDownloadError

_ARTIFACT_ID = re.compile(r"sha256:([0-9a-f]{64})\Z")


class LocalSourceArtifactStore:
    """Preserve validated ZIPs by SHA-256 and record every HTTP observation."""

    def __init__(
        self,
        http_client: httpx.AsyncClient,
        root: str | Path,
        *,
        observer: ArtifactObserver | None = None,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
        sleep: Sleeper = asyncio.sleep,
    ) -> None:
        self._http = http_client
        self._root = Path(root)
        self._staging = self._root.parent / f".{self._root.name}-staging"
        self._observer = observer
        self._clock = clock
        self._sleep = sleep
        self._memo: dict[str, SourceArtifact] = {}
        self._locks: dict[str, asyncio.Lock] = {}

    async def acquire(
        self, source_url: str, *, follow_redirects: bool = False
    ) -> SourceArtifact:
        """Acquire, validate, identify, and atomically publish one ZIP."""
        cached = self._memo.get(source_url)
        if cached is not None:
            return cached

        async with self._locks.setdefault(source_url, asyncio.Lock()):
            cached = self._memo.get(source_url)
            if cached is not None:
                return cached
            artifact = await self._acquire(source_url, follow_redirects)
            self._memo[source_url] = artifact
            if self._observer is not None:
                await self._observer(artifact)
            return artifact

    async def open(self, artifact_id: str) -> SourceArtifact:
        """Open content by identity without issuing an HTTP request."""
        artifact = await asyncio.to_thread(self._open, artifact_id)
        if self._observer is not None:
            await self._observer(artifact)
        return artifact

    async def _acquire(self, source_url: str, follow_redirects: bool) -> SourceArtifact:
        await asyncio.to_thread(self._prepare_directories)
        previous = await asyncio.to_thread(self._read_url_state, source_url)
        headers = self._conditional_headers(previous)
        stage = self._staging / f"{uuid4().hex}.zip"
        try:
            result = await download_zip(
                self._http,
                source_url,
                stage,
                follow_redirects=follow_redirects,
                sleep=self._sleep,
                headers=headers,
                allow_not_modified=bool(headers),
            )
            if result.status_code == httpx.codes.NOT_MODIFIED:
                artifact, replacement_result = await self._reuse_or_download(
                    source_url, stage, follow_redirects, previous
                )
                result = replacement_result or DownloadResult(
                    status_code=result.status_code,
                    etag=result.etag or self._string(previous, "etag"),
                    last_modified=(
                        result.last_modified or self._string(previous, "last_modified")
                    ),
                )
            else:
                artifact = await asyncio.to_thread(self._publish, stage)
            observed_at = self._clock()
            observed = self._with_observation(
                artifact, source_url, result, previous, observed_at
            )
            await asyncio.to_thread(
                self._record_observation, observed, result, observed_at
            )
            return observed
        finally:
            await asyncio.to_thread(stage.unlink, missing_ok=True)

    async def _reuse_or_download(
        self,
        source_url: str,
        stage: Path,
        follow_redirects: bool,
        previous: Mapping[str, object],
    ) -> tuple[SourceArtifact, DownloadResult | None]:
        artifact_id = self._string(previous, "artifact_id")
        if artifact_id is not None:
            try:
                return await asyncio.to_thread(self._open, artifact_id), None
            except FileNotFoundError:
                pass
        result = await download_zip(
            self._http,
            source_url,
            stage,
            follow_redirects=follow_redirects,
            sleep=self._sleep,
        )
        if result.status_code != httpx.codes.OK:
            raise CvmDownloadError(
                f"unexpected HTTP {result.status_code}: {source_url}"
            )
        return await asyncio.to_thread(self._publish, stage), result

    def _prepare_directories(self) -> None:
        self._root.mkdir(parents=True, exist_ok=True)
        self._staging.mkdir(parents=True, exist_ok=True)

    def _publish(self, stage: Path) -> SourceArtifact:
        try:
            with zipfile.ZipFile(stage) as archive:
                corrupt_member = archive.testzip()
        except (OSError, zipfile.BadZipFile) as exc:
            raise CvmDownloadError(
                "downloaded source is not a valid ZIP archive"
            ) from exc
        if corrupt_member is not None:
            raise CvmDownloadError(f"corrupt ZIP member: {corrupt_member}")

        digest = hashlib.sha256()
        byte_size = 0
        with stage.open("rb") as source:
            while chunk := source.read(1024 * 1024):
                digest.update(chunk)
                byte_size += len(chunk)
        sha256 = digest.hexdigest()
        artifact_id = f"sha256:{sha256}"
        destination = self._blob_path(sha256)
        destination.parent.mkdir(parents=True, exist_ok=True)
        if destination.exists():
            stage.unlink()
        else:
            stage.replace(destination)
        self._write_manifest(artifact_id, sha256, byte_size)
        return SourceArtifact(artifact_id, sha256, byte_size, destination)

    def _open(self, artifact_id: str) -> SourceArtifact:
        match = _ARTIFACT_ID.fullmatch(artifact_id)
        if match is None:
            raise ValueError(f"invalid source artifact id: {artifact_id}")
        sha256 = match.group(1)
        path = self._blob_path(sha256)
        if not path.is_file():
            raise FileNotFoundError(f"source artifact not found: {artifact_id}")
        return SourceArtifact(artifact_id, sha256, path.stat().st_size, path)

    def _blob_path(self, sha256: str) -> Path:
        return self._root / "sha256" / sha256[:2] / f"{sha256}.zip"

    def _manifest_path(self, sha256: str) -> Path:
        return self._root / "manifests" / "sha256" / sha256[:2] / f"{sha256}.json"

    def _write_manifest(self, artifact_id: str, sha256: str, byte_size: int) -> None:
        path = self._manifest_path(sha256)
        if path.exists():
            return
        self._write_json_atomic(
            path,
            {"artifact_id": artifact_id, "sha256": sha256, "byte_size": byte_size},
        )

    def _record_observation(
        self,
        artifact: SourceArtifact,
        result: DownloadResult,
        observed_at: datetime,
    ) -> None:
        assert artifact.source_url is not None
        assert artifact.downloaded_at is not None
        observation = {
            "artifact_id": artifact.artifact_id,
            "sha256": artifact.sha256,
            "byte_size": artifact.byte_size,
            "source_url": artifact.source_url,
            "downloaded_at": artifact.downloaded_at.isoformat(),
            "observed_at": observed_at.isoformat(),
            "etag": artifact.etag,
            "last_modified": artifact.last_modified,
            "http_status": result.status_code,
        }
        observation_path = self._root / "observations" / f"{uuid4().hex}.json"
        self._write_json_atomic(observation_path, observation)
        self._write_json_atomic(self._url_path(artifact.source_url), observation)

    def _read_url_state(self, source_url: str) -> Mapping[str, object]:
        path = self._url_path(source_url)
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return {}
        return value if isinstance(value, dict) else {}

    def _url_path(self, source_url: str) -> Path:
        digest = hashlib.sha256(source_url.encode()).hexdigest()
        return self._root / "urls" / f"{digest}.json"

    @staticmethod
    def _conditional_headers(previous: Mapping[str, object]) -> dict[str, str]:
        headers: dict[str, str] = {}
        etag = LocalSourceArtifactStore._string(previous, "etag")
        modified = LocalSourceArtifactStore._string(previous, "last_modified")
        if etag is not None:
            headers["If-None-Match"] = etag
        if modified is not None:
            headers["If-Modified-Since"] = modified
        return headers

    @staticmethod
    def _string(values: Mapping[str, object], key: str) -> str | None:
        value = values.get(key)
        return value if isinstance(value, str) and value else None

    def _with_observation(
        self,
        artifact: SourceArtifact,
        source_url: str,
        result: DownloadResult,
        previous: Mapping[str, object],
        observed_at: datetime,
    ) -> SourceArtifact:
        downloaded_at = (
            self._datetime(previous, "downloaded_at")
            if result.status_code == httpx.codes.NOT_MODIFIED
            else None
        )
        return SourceArtifact(
            artifact_id=artifact.artifact_id,
            sha256=artifact.sha256,
            byte_size=artifact.byte_size,
            path=artifact.path,
            source_url=source_url,
            downloaded_at=downloaded_at or observed_at,
            etag=result.etag,
            last_modified=result.last_modified,
        )

    @staticmethod
    def _datetime(values: Mapping[str, object], key: str) -> datetime | None:
        value = LocalSourceArtifactStore._string(values, key)
        if value is None:
            return None
        try:
            return datetime.fromisoformat(value)
        except ValueError:
            return None

    @staticmethod
    def _write_json_atomic(path: Path, payload: Mapping[str, object]) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_name(f".{path.name}.{uuid4().hex}.part")
        temporary.write_text(
            json.dumps(payload, sort_keys=True, separators=(",", ":")) + "\n",
            encoding="utf-8",
        )
        temporary.replace(path)
