"""Verify native desktop artifacts on their target operating system.

Requires macOS disk-image tools or Windows. Runs only explicit help commands;
never installs services, creates shortcuts, or changes an existing installation.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import platform
import plistlib
import subprocess
import tempfile
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
PREFIXES = ("smaug-installer", "smaug-backend-installer")


def verify_checksum(path: Path) -> None:
    """Require the companion checksum to match the complete artifact."""
    expected = path.with_name(path.name + ".sha256").read_text().split()
    with path.open("rb") as stream:
        actual = hashlib.file_digest(stream, "sha256").hexdigest()
    if expected != [actual, path.name]:
        raise ValueError(f"Checksum mismatch: {path.name}")


def verify_payload(path: Path) -> None:
    """Verify the enclosed dumps with bounded buffers."""
    with path.open("rb") as stream:
        footer = b"SMAUG-PREPARED-1"
        stream.seek(-len(footer), 2)
        if stream.read() != footer:
            raise ValueError(f"Missing prepared payload: {path.name}")
        with zipfile.ZipFile(stream) as archive:
            snapshot = archive.read("snapshot-manifest.json")
            release = json.loads(archive.read("manifest.json"))
            if (
                hashlib.sha256(snapshot).hexdigest()
                != release["snapshot"]["manifest_sha256"]
            ):
                raise ValueError(f"Snapshot manifest checksum mismatch: {path.name}")
            for payload in json.loads(snapshot)["payloads"]:
                name = payload["name"]
                with archive.open(name) as dump:
                    digest = hashlib.file_digest(dump, "sha256").hexdigest()
                if (
                    archive.getinfo(name).file_size != payload["size_bytes"]
                    or digest != payload["sha256"]
                ):
                    raise ValueError(f"Embedded dump checksum mismatch: {name}")


def verify_macos(
    output: Path, architectures: tuple[str, ...], *, prepared: bool
) -> None:
    """Mount every DMG and verify its app, license and native architecture."""
    native = "arm64" if platform.machine() == "arm64" else "amd64"
    for prefix in PREFIXES:
        for arch in architectures:
            image = output / f"{prefix}-macos-{arch}.dmg"
            verify_checksum(image)
            subprocess.run(["hdiutil", "verify", str(image)], check=True)
            with tempfile.TemporaryDirectory(prefix="smaug-dmg-verify-") as temporary:
                mount = Path(temporary) / "mounted"
                mount.mkdir()
                subprocess.run(
                    [
                        "hdiutil",
                        "attach",
                        "-readonly",
                        "-nobrowse",
                        "-mountpoint",
                        str(mount),
                        str(image),
                    ],
                    check=True,
                )
                try:
                    apps = list(mount.glob("*.app"))
                    if len(apps) != 1:
                        raise ValueError(f"Expected exactly one app: {image.name}")
                    contents = apps[0] / "Contents"
                    info = plistlib.loads((contents / "Info.plist").read_bytes())
                    if info["CFBundlePackageType"] != "APPL":
                        raise ValueError("Invalid application bundle")
                    binary = contents / "MacOS" / info["CFBundleExecutable"]
                    if not binary.stat().st_mode & 0o111:
                        raise ValueError("Application launcher is not executable")
                    if (contents / "Resources/LICENSE").read_bytes() != (
                        ROOT / "LICENSE"
                    ).read_bytes():
                        raise ValueError("Application license differs from repository")
                    subprocess.run(
                        [
                            "lipo",
                            str(binary),
                            "-verify_arch",
                            "x86_64" if arch == "amd64" else arch,
                        ],
                        check=True,
                    )
                    if arch == native:
                        subprocess.run([str(binary), "--help"], check=True, timeout=30)
                    if prepared:
                        verify_payload(contents / "Resources/prepared.zip")
                    if (mount / "Applications").readlink() != Path("/Applications"):
                        raise ValueError("Missing Applications shortcut")
                finally:
                    subprocess.run(["hdiutil", "detach", str(mount)], check=True)
            print(f"Verified {image.name}")


def verify_windows(
    output: Path, architectures: tuple[str, ...], *, prepared: bool
) -> None:
    """Verify both PE architectures and execute each native graphical launcher."""
    native = "arm64" if platform.machine().lower() in {"arm64", "aarch64"} else "amd64"
    for prefix in PREFIXES:
        for arch in architectures:
            suffix = "-arm64" if arch == "arm64" else ""
            binary = output / f"{prefix}{suffix}.exe"
            verify_checksum(binary)
            with binary.open("rb") as stream:
                if stream.read(2) != b"MZ":
                    raise ValueError("Missing Windows executable header")
                stream.seek(0x3C)
                offset = int.from_bytes(stream.read(4), "little")
                stream.seek(offset)
                if stream.read(4) != b"PE\x00\x00":
                    raise ValueError("Missing PE signature")
                machine = int.from_bytes(stream.read(2), "little")
                if machine != {"amd64": 0x8664, "arm64": 0xAA64}[arch]:
                    raise ValueError(f"Wrong Windows architecture: {binary.name}")
                stream.seek(offset + 24 + 68)
                if int.from_bytes(stream.read(2), "little") != 2:
                    raise ValueError("Launcher must use the Windows GUI subsystem")
            if arch == native and platform.system() == "Windows":
                subprocess.run([str(binary), "--help"], check=True, timeout=30)
            if prepared:
                verify_payload(binary)
            print(f"Verified {binary.name}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--system", choices=("macos", "windows"), required=True)
    parser.add_argument("--arch", choices=("amd64", "arm64"))
    parser.add_argument("--prepared", action="store_true")
    args = parser.parse_args()
    verify = verify_macos if args.system == "macos" else verify_windows
    verify(
        args.input.resolve(),
        (args.arch,) if args.arch else ("amd64", "arm64"),
        prepared=args.prepared,
    )


if __name__ == "__main__":
    main()
