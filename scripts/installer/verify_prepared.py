"""Install a Linux native package into a disposable project and measure RAM."""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import tempfile
import time
import urllib.request
from pathlib import Path
from typing import Any

from smoke import port, request

DOCKER = ["docker"]
UNITS = {"B": 1, "KiB": 1024, "MiB": 1024**2, "GiB": 1024**3, "TiB": 1024**4}


def pending_config(root: Path) -> dict[str, Any] | None:
    """Get the newly allocated Docker project before the installer publishes it."""
    try:
        return json.loads((root / "config.json").read_text())
    except FileNotFoundError:
        try:
            pending = json.loads((root / "pending.json").read_text())
        except FileNotFoundError:
            return None
        return pending["import"]["config"]


def docker_output(*args: str) -> str:
    return subprocess.check_output([*DOCKER, *args], text=True).strip()


def project_ram(project: str) -> int | None:
    ids = docker_output(
        "ps", "-q", "--filter", f"label=com.docker.compose.project={project}"
    ).splitlines()
    if not ids:
        return 0
    # Docker CLI memory usage removes reclaimable page cache on Linux.
    try:
        sample = docker_output(
            "stats", "--no-stream", "--format", "{{.MemUsage}}", *ids
        )
    except subprocess.CalledProcessError:
        return None  # A restore helper can exit between listing and sampling.
    total = 0
    for row in sample.splitlines():
        match = re.fullmatch(r"(\d+(?:\.\d+)?)([A-Za-z]+)", row.split()[0])
        if match is None:
            raise ValueError(f"Unrecognized Docker memory sample: {row}")
        total += round(float(match[1]) * UNITS[match[2]])
    return total


def process_tree_rss(pid: int) -> int:
    path = Path("/proc") / str(pid)
    try:
        status = (path / "status").read_text()
        children = (path / "task" / str(pid) / "children").read_text().split()
    except (FileNotFoundError, ProcessLookupError):
        return 0
    match = re.search(r"^VmRSS:\s+(\d+) kB", status, re.MULTILINE)
    rss = int(match[1]) * 1024 if match else 0
    return rss + sum(process_tree_rss(int(child)) for child in children)


def cleanup(root: Path) -> None:
    config = pending_config(root)
    if config is None:
        return
    label = f"label=com.docker.compose.project={config['project']}"
    for kind, listing, remove in (
        ("container", ("ls", "--all"), ("rm", "-f")),
        ("network", ("ls",), ("rm",)),
        ("volume", ("ls",), ("rm",)),
    ):
        names = docker_output(kind, *listing, "-q", "--filter", label).splitlines()
        if names:
            docker_output(kind, *remove, *names)


def partial_mongo_import(project: str) -> bool:
    try:
        mongo = docker_output(
            "ps",
            "-q",
            "--filter",
            f"label=com.docker.compose.project={project}",
            "--filter",
            "label=com.docker.compose.service=mongo",
            "--filter",
            "health=healthy",
        )
        return (
            bool(mongo)
            and docker_output(
                "exec",
                mongo,
                "mongosh",
                "--quiet",
                "smaug",
                "--eval",
                "print(db.source_input_rows.estimatedDocumentCount() > 0);",
            )
            == "true"
        )
    except subprocess.CalledProcessError:
        return False  # The container can still be starting.


def verify(
    package: Path, edition: str, report: Path | None, *, exercise_recovery: bool
) -> None:
    prefix = "smaug-installer" if edition == "full" else "smaug-backend-installer"
    with tempfile.TemporaryDirectory(prefix="smaug-prepared-") as temp:
        base = Path(temp)
        subprocess.run(["dpkg-deb", "-x", package, base], check=True)
        launcher = base / "usr" / "lib" / prefix / prefix
        root, home = base / "installation", base / "home"
        home.mkdir()
        api, web = port(), port()
        command = [str(launcher), "--dir", str(root)]
        env = {**os.environ, "HOME": str(home)}
        start = time.monotonic()
        peak_host = peak_docker = peak_combined = samples = 0
        install = [
            *command,
            "installer",
            "--api-port",
            str(api),
            "--web-port",
            str(web),
        ]
        proc = subprocess.Popen(install, env=env)
        resumed_project = None
        try:
            while proc.poll() is None:
                rss = process_tree_rss(proc.pid)
                config = pending_config(root)
                docker = project_ram(config["project"]) if config else 0
                peak_host = max(peak_host, rss)
                if docker is not None:
                    peak_docker = max(peak_docker, docker)
                    peak_combined = max(peak_combined, rss + docker)
                    samples += 1
                if (
                    exercise_recovery
                    and resumed_project is None
                    and config
                    and (root / "pending.json").exists()
                    and not (root / "prepared.json").exists()
                    and partial_mongo_import(config["project"])
                ):
                    proc.kill()
                    proc.wait(timeout=15)
                    assert subprocess.run([*command, "check"], env=env).returncode != 0
                    assert not (root / "prepared.json").exists()
                    assert (root / "snapshot-import" / "postgres.dump").is_file()
                    resumed_project = config["project"]
                    start = time.monotonic()
                    peak_host = peak_docker = peak_combined = samples = 0
                    proc = subprocess.Popen(install, env=env)
                time.sleep(0.25)
            if proc.returncode != 0:
                raise subprocess.CalledProcessError(proc.returncode, proc.args)
            elapsed = time.monotonic() - start
            subprocess.run([*command, "check"], env=env, check=True)
            assert not (root / "snapshot-import").exists()
            assert not (root / "pending.json").exists()
            receipt = json.loads((root / "prepared.json").read_text())
            current = json.loads((root / "current.json").read_text())
            assert current["edition"] == edition
            manifest = json.loads(
                (root / "releases" / current["version"] / "manifest.json").read_text()
            )
            assert receipt["revision"] == manifest["snapshot"]["data_revision"]
            assert request(api, "/health")["commit"] == manifest["commit"]
            assert request(api, "/analysis/PETR4")
            assert request(api, "/portfolio") == []
            assert request(api, "/data/revision")["revision"]
            if edition == "full":
                with urllib.request.urlopen(
                    f"http://127.0.0.1:{web}/ticker/PETR4", timeout=60
                ) as page:
                    assert b"PETR4" in page.read()
                assert request(web, "/api/data-revision")["revision"]
            if exercise_recovery:
                assert receipt["project"] == resumed_project
                assert request(api, "/portfolio/PETR4", "POST")["ticker"] == "PETR4"
                subprocess.run(install, env=env, check=True)
                assert any(
                    row["ticker"] == "PETR4" for row in request(api, "/portfolio")
                )
                assert not (root / "snapshot-import").exists()
            result = {
                "edition": edition,
                "package_bytes": package.stat().st_size,
                "snapshot_revision": receipt["revision"],
                "installer_seconds": round(elapsed, 3),
                "samples": samples,
                "resumed_interrupted_import": resumed_project is not None,
                "peak_host_installer_and_children_rss_bytes": peak_host,
                "peak_compose_container_working_set_bytes": peak_docker,
                "peak_sampled_combined_bytes": peak_combined,
            }
            print(json.dumps(result, indent=2), flush=True)
            if report is not None:
                report.write_text(json.dumps(result, indent=2) + "\n")
        finally:
            if proc.poll() is None:
                proc.terminate()
                proc.wait(timeout=10)
            cleanup(root)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--package", type=Path, required=True)
    parser.add_argument("--edition", choices=("backend", "full"), required=True)
    parser.add_argument("--report", type=Path)
    parser.add_argument("--exercise-recovery", action="store_true")
    args = parser.parse_args()
    verify(
        args.package.resolve(),
        args.edition,
        args.report,
        exercise_recovery=args.exercise_recovery,
    )


if __name__ == "__main__":
    main()
