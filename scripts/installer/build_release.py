"""Build immutable offline launchers and Compose bundles (requires Go and uv)."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
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


def sha256(path: Path) -> str:
    return hashlib.file_digest(path.open("rb"), "sha256").hexdigest()


def build() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--commit", required=True)
    parser.add_argument("--version", required=True)
    parser.add_argument("--backend", required=True)
    parser.add_argument("--frontend", required=True)
    parser.add_argument("--output", type=Path, default=ROOT / "dist" / "installers")
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
    output: Path = args.output.resolve()
    output.mkdir(parents=True, exist_ok=True)
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
    images = json.loads((ROOT / "installer" / "images.json").read_text())
    images.update(backend=args.backend, frontend=args.frontend)
    heads = ScriptDirectory.from_config(Config(str(ROOT / "alembic.ini"))).get_heads()
    if len(heads) != 1:
        raise ValueError("release requires exactly one Alembic head")
    editions: tuple[SetupEdition, ...] = ("backend", "full")
    for edition in editions:
        bundle = output / edition
        bundle.mkdir(exist_ok=True)
        for path in binaries.iterdir():
            shutil.copy2(path, bundle / path.name)
        for name in ("compose.yaml", "install.sh", "install.ps1"):
            shutil.copy2(ROOT / "installer" / name, bundle / name)
        (bundle / "edition.txt").write_text(edition + "\n")
        (bundle / "SETUP.md").write_text(
            render_setup_guide(edition=edition), encoding="utf-8"
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
            },
        }
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
    print(output)


if __name__ == "__main__":
    build()
