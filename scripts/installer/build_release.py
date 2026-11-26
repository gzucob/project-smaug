"""Build immutable managers, graphical entrypoints and Compose bundles."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tarfile
import zipfile
from pathlib import Path

from alembic.config import Config
from alembic.script import ScriptDirectory

from smaug.shared.mcp_setup import SetupEdition, render_setup_guide

ROOT = Path(__file__).resolve().parents[2]
TARGETS = [
    (system, arch)
    for system in ("linux", "darwin", "windows")
    for arch in ("amd64", "arm64")
]
EDITIONS: tuple[SetupEdition, ...] = ("backend", "full")
NATIVE_LIMIT = 2**31
PREPARED_FOOTER = b"SMAUG-PREPARED-1"


def sha256(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def snapshot_contract(directory: Path) -> dict[str, str | int]:
    """Require the full phase-5 certification and this checkout's compatibility."""
    subprocess.run(
        [
            sys.executable,
            str(ROOT / "scripts/distribution_snapshot.py"),
            "verify",
            str(directory),
        ],
        cwd=ROOT,
        check=True,
    )
    manifest = directory / "manifest.json"
    metadata = json.loads(manifest.read_text())
    images = metadata["validation"]["database_images"]
    for database in ("mongo", "postgres"):
        if not re.fullmatch(r"sha256:[a-f0-9]{64}", images[database]):
            raise ValueError(f"Missing certified database image digest: {database}")
    return {
        "manifest_sha256": sha256(manifest),
        "data_revision": metadata["data_revision"],
        "mongo_image": f"mongo@{images['mongo']}",
        "postgres_image": f"postgres@{images['postgres']}",
        **{
            field: metadata["compatibility"][field]
            for field in (
                "code_sha256",
                "calculation_contract",
                "source_parser",
                "mongo_major",
                "postgres_major",
            )
        },
    }


def build() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--commit", required=True)
    parser.add_argument("--version", required=True)
    parser.add_argument("--backend", required=True)
    parser.add_argument("--frontend", required=True)
    parser.add_argument("--output", type=Path, default=ROOT / "dist" / "installers")
    parser.add_argument("--snapshot", type=Path)
    parser.add_argument("--test-fixture", action="store_true")
    parser.add_argument("--runtime-only", action="store_true")
    parser.add_argument("--native-only", action="store_true")
    parser.add_argument("--system", choices=("linux", "darwin", "windows"))
    parser.add_argument("--arch", choices=("amd64", "arm64"))
    args = parser.parse_args()
    if not re.fullmatch(r"\d{4}\.\d{2}\.\d{2}-[a-f0-9]{12}", args.version):
        parser.error("invalid version")
    if not re.fullmatch(r"[a-f0-9]{40}", args.commit) or not args.version.endswith(
        args.commit[:12]
    ):
        parser.error("version must identify the commit")
    for image in (args.backend, args.frontend):
        if not re.fullmatch(r"[a-z0-9./:_-]+@sha256:[a-f0-9]{64}", image):
            parser.error("images must be pinned by digest")
    if bool(args.snapshot) == args.test_fixture:
        parser.error("supply a validated --snapshot or explicitly use --test-fixture")
    if args.native_only and (args.runtime_only or not args.system or not args.arch):
        parser.error("--native-only requires one --system and --arch")
    output: Path = args.output.resolve()
    output.mkdir(parents=True, exist_ok=True)
    snapshot = args.snapshot.resolve() if args.snapshot else None
    contract = snapshot_contract(snapshot) if snapshot is not None else None
    if not args.native_only:
        build_runtime(args, output, contract)
    if not args.runtime_only:
        for system, arch in TARGETS:
            if args.system not in (None, system) or args.arch not in (None, arch):
                continue
            build_native(args, output, system, arch, snapshot, contract)
    print(output)


def build_runtime(
    args: argparse.Namespace, output: Path, contract: dict[str, str | int] | None
) -> None:
    binaries = output / "binaries"
    binaries.mkdir(exist_ok=True)
    for system, arch in TARGETS:
        name = f"smaugctl-{system}-{arch}" + (".exe" if system == "windows" else "")
        subprocess.run(
            [
                "go",
                "build",
                "-trimpath",
                "-ldflags=-s -w",
                "-o",
                str(binaries / name),
                ".",
            ],
            cwd=ROOT / "installer",
            env={**os.environ, "GOOS": system, "GOARCH": arch, "CGO_ENABLED": "0"},
            check=True,
        )
        if system == "windows":
            subprocess.run(
                [
                    "go",
                    "build",
                    "-trimpath",
                    "-ldflags=-s -w -H=windowsgui -X main.guiBuild=true",
                    "-o",
                    str(binaries / f"smaug-desktop-windows-{arch}.exe"),
                    ".",
                ],
                cwd=ROOT / "installer",
                env={**os.environ, "GOOS": system, "GOARCH": arch, "CGO_ENABLED": "0"},
                check=True,
            )
    images = json.loads((ROOT / "installer" / "images.json").read_text())
    images.update(backend=args.backend, frontend=args.frontend)
    heads = ScriptDirectory.from_config(Config(str(ROOT / "alembic.ini"))).get_heads()
    if len(heads) != 1:
        raise ValueError("release requires exactly one Alembic head")
    for edition in EDITIONS:
        bundle = output / edition
        bundle.mkdir(exist_ok=True)
        for path in binaries.iterdir():
            shutil.copy2(path, bundle / path.name)
        for name in ("compose.yaml", "install.sh", "install.ps1"):
            shutil.copy2(ROOT / "installer" / name, bundle / name)
        shutil.copy2(ROOT / "LICENSE", bundle / "LICENSE")
        (bundle / "edition.txt").write_text(edition + "\n")
        (bundle / "SETUP.md").write_text(
            render_setup_guide(edition=edition),
            encoding="utf-8",
        )
        manifest = {
            "version": args.version,
            "commit": args.commit,
            "schema": heads[0],
            "images": images,
            "files": {
                path.name: sha256(path)
                for path in sorted(bundle.iterdir())
                if path.name not in {"manifest.json", "checksums.txt"}
                and (contract is None or path.name not in {"edition.txt", "SETUP.md"})
            },
        }
        if contract is not None:
            manifest["snapshot"] = contract
        (bundle / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
        (bundle / "checksums.txt").write_text(
            "".join(
                f"{sha256(path)}  {path.name}\n"
                for path in sorted(bundle.iterdir())
                if path.name != "checksums.txt"
            )
        )
        archive_name = f"smaug-{edition}-{args.version}"
        archive = output / f"{archive_name}.zip"
        with zipfile.ZipFile(archive, "w", zipfile.ZIP_DEFLATED) as zipped:
            for path in sorted(bundle.iterdir()):
                zipped.write(path, path.name)
        tar_path = output / f"{archive_name}.tar.gz"
        with tarfile.open(tar_path, "w:gz") as tar:
            for path in sorted(bundle.iterdir()):
                tar.add(path, arcname=path.name)
        for path in (archive, tar_path):
            path.with_name(path.name + ".sha256").write_text(
                f"{sha256(path)}  {path.name}\n"
            )


def pack_payload(path: Path, bundle: Path, snapshot: Path) -> None:
    """Append an independently readable ZIP without loading dumps into RAM."""
    with path.open("r+b" if path.exists() else "w+b") as stream:
        stream.seek(0, os.SEEK_END)
        with zipfile.ZipFile(stream, "w", zipfile.ZIP_DEFLATED) as archive:
            for entry in sorted(bundle.iterdir()):
                archive.write(entry, entry.name)
            archive.write(snapshot / "manifest.json", "snapshot-manifest.json")
            for name in ("mongo.archive.xz", "postgres.dump"):
                archive.write(snapshot / name, name, compress_type=zipfile.ZIP_STORED)
        stream.write(PREPARED_FOOTER)
    check_native_size(path)


def check_native_size(path: Path) -> None:
    if path.stat().st_size >= NATIVE_LIMIT:
        raise ValueError(f"Native package exceeds the release asset limit: {path.name}")


def build_native(
    args: argparse.Namespace,
    output: Path,
    system: str,
    arch: str,
    snapshot: Path | None,
    contract: dict[str, str | int] | None,
) -> None:
    from package_desktop import linux_package, macos_package

    for edition in EDITIONS:
        name = "smaug-installer" if edition == "full" else "smaug-backend-installer"
        if system == "windows":
            suffix = "-arm64" if arch == "arm64" else ""
            executable = output / f"{name}{suffix}.exe"
        else:
            platform = "macos" if system == "darwin" else system
            executable = output / f"{name}-{platform}-{arch}"
        bundle = output / edition
        manifest = json.loads((bundle / "manifest.json").read_text())
        if (
            manifest["version"] != args.version
            or manifest["commit"] != args.commit
            or manifest["images"]["backend"] != args.backend
            or manifest["images"]["frontend"] != args.frontend
            or manifest.get("snapshot") != contract
        ):
            raise ValueError(
                f"Runtime bundle differs from the requested release: {edition}"
            )
        flags = [
            "-s -w",
            f"-X main.releaseVersion={args.version}",
            f"-X main.defaultEdition={edition}",
        ]
        if system == "windows":
            flags.append("-H=windowsgui -X main.guiBuild=true")
        if snapshot is not None:
            flags.append("-X main.embeddedRequired=true")
        # Go can reuse an output with the same build ID, including an old
        # executable that already has a prepared ZIP appended to it.
        executable.unlink(missing_ok=True)
        subprocess.run(
            [
                "go",
                "build",
                "-trimpath",
                "-ldflags=" + " ".join(flags),
                "-o",
                str(executable),
                ".",
            ],
            cwd=ROOT / "installer",
            env={**os.environ, "GOOS": system, "GOARCH": arch, "CGO_ENABLED": "0"},
            check=True,
        )
        # Keep the Mach-O intact. The DMG contains a single app with its
        # compressed snapshot in Resources, beside the executable.
        sidecar = executable.with_suffix(".prepared.zip")
        if snapshot is not None:
            if system == "darwin":
                sidecar.unlink(missing_ok=True)
            pack_payload(
                sidecar if system == "darwin" else executable, bundle, snapshot
            )
        if system == "linux":
            check_native_size(
                linux_package(
                    output, name, arch, args.version, prepared=snapshot is not None
                )
            )
        elif system == "darwin" and snapshot is not None:
            check_native_size(
                macos_package(output, name, arch, args.version, payload=sidecar)
            )
            sidecar.unlink()
        if snapshot is not None and system == "darwin":
            executable.unlink()
        else:
            executable.with_name(executable.name + ".sha256").write_text(
                f"{sha256(executable)}  {executable.name}\n"
            )


if __name__ == "__main__":
    build()
