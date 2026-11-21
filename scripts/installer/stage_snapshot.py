"""Unpack the certified snapshot handoff on the release staging runner."""

from __future__ import annotations

import shutil
import sys
import zipfile
from pathlib import Path

NAMES = {"manifest.json", "mongo.archive.xz", "postgres.dump"}


def unpack(archive: Path, destination: Path) -> None:
    """Copy the three expected entries with a bounded total and fixed paths."""
    with zipfile.ZipFile(archive) as source:
        entries = source.infolist()
        if len(entries) != len(NAMES) or {entry.filename for entry in entries} != NAMES:
            raise ValueError("Expected exactly the three certified snapshot files")
        if sum(entry.file_size for entry in entries) >= 2**31:
            raise ValueError("Snapshot ZIP exceeds the native asset budget")
        if source.getinfo("manifest.json").file_size >= 2**20:
            raise ValueError("Snapshot manifest exceeds the installer limit")
        destination.mkdir()
        for entry in entries:
            with (
                source.open(entry) as infile,
                (destination / entry.filename).open("xb") as outfile,
            ):
                shutil.copyfileobj(infile, outfile, length=1024 * 1024)


if __name__ == "__main__":
    unpack(Path(sys.argv[1]), Path(sys.argv[2]))
