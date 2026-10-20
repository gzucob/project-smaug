"""Package native Smaug launchers for graphical installation on Linux and macOS."""

from __future__ import annotations

import argparse
import hashlib
import plistlib
import shutil
import subprocess
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def checksum(path: Path) -> None:
    digest = hashlib.file_digest(path.open("rb"), "sha256").hexdigest()
    path.with_name(path.name + ".sha256").write_text(f"{digest}  {path.name}\n")


def linux_package(output: Path, prefix: str, arch: str, version: str) -> Path:
    """Build a desktop-menu package without running application setup as root."""
    with tempfile.TemporaryDirectory(prefix="smaug-deb-") as temporary:
        root = Path(temporary)
        root.chmod(0o755)
        control = root / "DEBIAN"
        control.mkdir()
        control.chmod(0o755)
        control.joinpath("control").write_text(
            f"Package: {prefix}\nVersion: {version}\n"
            "Section: utils\nPriority: optional\n"
            f"Architecture: {arch}\nMaintainer: Smaug <noreply@smaug.local>\n"
            "Depends: xdg-utils\n"
            "Description: Graphical installer and manager for Smaug\n"
            " Prepares public CVM/B3 data in local Docker containers.\n"
        )
        binary = root / "usr" / "lib" / prefix / prefix
        binary.parent.mkdir(parents=True)
        shutil.copyfile(output / f"{prefix}-linux-{arch}", binary)
        binary.chmod(0o755)
        icon = root / "usr/share/icons/hicolor/scalable/apps" / f"{prefix}.svg"
        icon.parent.mkdir(parents=True)
        shutil.copyfile(ROOT / "installer/ui/icon.svg", icon)
        copyright_file = root / "usr/share/doc" / prefix / "copyright"
        copyright_file.parent.mkdir(parents=True)
        shutil.copyfile(ROOT / "LICENSE", copyright_file)
        desktop = root / "usr/share/applications" / f"{prefix}.desktop"
        desktop.parent.mkdir(parents=True)
        name = (
            "Instalar Smaug"
            if prefix == "smaug-installer"
            else "Instalar Smaug Backend"
        )
        desktop.write_text(
            f"[Desktop Entry]\nType=Application\nName={name}\n"
            f"Exec=/usr/lib/{prefix}/{prefix} gui\nIcon={prefix}\n"
            "Terminal=false\nCategories=Office;Finance;\n"
        )
        for directory in root.rglob("*"):
            if directory.is_dir():
                directory.chmod(0o755)
        package = output / f"{prefix}-linux-{arch}.deb"
        subprocess.run(
            ["dpkg-deb", "--root-owner-group", "--build", str(root), str(package)],
            check=True,
        )
        checksum(package)
        return package


def macos_package(output: Path, prefix: str, arch: str, version: str) -> Path:
    """Build an app bundle and DMG with macOS's own disk-image tooling."""
    with tempfile.TemporaryDirectory(prefix="smaug-dmg-") as temporary:
        root = Path(temporary)
        root.chmod(0o755)
        name = (
            "Smaug Installer"
            if prefix == "smaug-installer"
            else "Smaug Backend Installer"
        )
        app = root / f"{name}.app" / "Contents"
        binary = app / "MacOS" / "Smaug"
        binary.parent.mkdir(parents=True)
        shutil.copyfile(output / f"{prefix}-macos-{arch}", binary)
        binary.chmod(0o755)
        resources = app / "Resources"
        resources.mkdir()
        shutil.copyfile(ROOT / "LICENSE", resources / "LICENSE")
        with (app / "Info.plist").open("wb") as stream:
            plistlib.dump(
                {
                    "CFBundleName": name,
                    "CFBundleIdentifier": f"io.smaug.{prefix}",
                    "CFBundleExecutable": "Smaug",
                    "CFBundlePackageType": "APPL",
                    "CFBundleShortVersionString": version.split("-")[0],
                    "CFBundleVersion": "1",
                    "LSUIElement": True,
                    "LSMinimumSystemVersion": "12.0",
                    "NSHighResolutionCapable": True,
                },
                stream,
            )
        (root / "Applications").symlink_to("/Applications", target_is_directory=True)
        package = output / f"{prefix}-macos-{arch}.dmg"
        subprocess.run(
            [
                "hdiutil",
                "create",
                "-ov",
                "-format",
                "UDZO",
                "-volname",
                name,
                "-srcfolder",
                str(root),
                str(package),
            ],
            check=True,
        )
        checksum(package)
        return package


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--version", required=True)
    parser.add_argument("--system", choices=("linux", "macos"), required=True)
    args = parser.parse_args()
    build = linux_package if args.system == "linux" else macos_package
    for prefix in ("smaug-installer", "smaug-backend-installer"):
        for arch in ("amd64", "arm64"):
            print(build(args.input.resolve(), prefix, arch, args.version))


if __name__ == "__main__":
    main()
