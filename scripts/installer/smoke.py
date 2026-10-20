"""Exercise disposable backend/full installations; never connect to checkout DBs.

Requires prebuilt bundles and Docker. Removes only the Compose projects whose
configuration was created inside this invocation's temporary directory.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import platform
import queue
import shutil
import socket
import subprocess
import tempfile
import threading
import time
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any


def run(*args: str, capture: bool = False, data: str | None = None) -> str:
    result = subprocess.run(
        args,
        input=data,
        text=True,
        stdout=subprocess.PIPE if capture else None,
        check=True,
    )
    return result.stdout or ""


def port() -> int:
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        return int(listener.getsockname()[1])


def request(port_number: int, path: str, method: str = "GET") -> Any:
    req = urllib.request.Request(f"http://127.0.0.1:{port_number}{path}", method=method)
    with urllib.request.urlopen(req, timeout=30) as response:
        data = response.read()
        return json.loads(data) if data else None


def check_gui(command: list[str], install: Path) -> None:
    """Exercise the real local controller against this disposable installation."""
    if platform.system() != "Linux":
        return
    with tempfile.TemporaryDirectory(prefix="smaug-gui-browser-") as temporary:
        browser = Path(temporary) / "xdg-open"
        browser.write_text('#!/bin/sh\nprintf "%s\\n" "$1" >> "$SMAUG_GUI_OPEN_LOG"\n')
        browser.chmod(0o700)
        env = {
            **os.environ,
            "PATH": temporary + os.pathsep + os.environ["PATH"],
            "SMAUG_GUI_OPEN_LOG": str(Path(temporary) / "opened.log"),
        }
        process = subprocess.Popen([*command, "gui"], env=env)
        try:
            session = install / "gui-session.json"
            deadline = time.monotonic() + 20
            while not session.exists():
                assert process.poll() is None, "GUI exited before opening"
                assert time.monotonic() < deadline, "GUI did not open"
                time.sleep(0.1)
            url = json.loads(session.read_text())["url"]
            origin = urllib.parse.urlunsplit(
                (*urllib.parse.urlsplit(url)[:2], "", "", "")
            )

            def status() -> Any:
                with urllib.request.urlopen(url + "status", timeout=5) as response:
                    return json.loads(response.read())

            def action(name: str) -> None:
                req = urllib.request.Request(
                    url + "action",
                    data=json.dumps({"action": name}).encode(),
                    headers={"Content-Type": "application/json", "Origin": origin},
                )
                with urllib.request.urlopen(req, timeout=5) as response:
                    assert response.status == 202

            while not status()["running"]:
                assert time.monotonic() < deadline, "GUI did not detect prepared data"
                time.sleep(0.1)
            with urllib.request.urlopen(url, timeout=5) as page:
                assert b"Instalar o Smaug" in page.read()
            subprocess.run([*command, "gui"], env=env, check=True, timeout=10)
            assert json.loads(session.read_text())["url"] == url
            for name, running in (("stop", False), ("start", True)):
                action(name)
                deadline = time.monotonic() + 90
                while True:
                    state = status()
                    if not state["busy"]:
                        assert not state["error"], state
                        assert state["running"] == running, state
                        break
                    assert time.monotonic() < deadline, f"GUI {name} did not finish"
                    time.sleep(0.2)
            action("open")
            action("close")
            assert process.wait(timeout=10) == 0
            assert not session.exists()
        finally:
            if process.poll() is None:
                process.terminate()
                process.wait(timeout=10)


def check_mcp(
    command: list[str],
    *,
    expected_status: str,
    ticker: str | None = None,
    setup_edition: str = "backend",
) -> None:
    """Exercise the installed stdio command using only the Python standard library."""
    process = subprocess.Popen(
        [*command, "mcp", "run"],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        text=True,
    )
    assert process.stdin is not None
    assert process.stdout is not None
    lines: queue.Queue[str] = queue.Queue()

    def read_output() -> None:
        assert process.stdout is not None
        for line in process.stdout:
            lines.put(line)
        lines.put("")

    thread = threading.Thread(target=read_output, daemon=True)
    thread.start()

    def send(message: dict[str, Any]) -> None:
        assert process.stdin is not None
        process.stdin.write(json.dumps(message) + "\n")
        process.stdin.flush()

    def call(identifier: int, method: str, params: dict[str, Any]) -> Any:
        send({"jsonrpc": "2.0", "id": identifier, "method": method, "params": params})
        while True:
            line = lines.get(timeout=60)
            assert line, f"MCP exited unexpectedly: {process.poll()}"
            response = json.loads(line)
            if response.get("id") == identifier:
                assert "error" not in response, response
                return response["result"]

    try:
        initialized = call(
            1,
            "initialize",
            {
                "protocolVersion": "2026-07-28",
                "capabilities": {},
                "clientInfo": {"name": "smaug-installer-smoke", "version": "1"},
            },
        )
        assert initialized["serverInfo"]["name"] == "smaug"
        send({"jsonrpc": "2.0", "method": "notifications/initialized"})
        discovered = call(2, "tools/list", {})
        assert {"get_setup_guide", "get_service_status", "list_portfolio"} <= {
            tool["name"] for tool in discovered["tools"]
        }
        guide = call(
            3,
            "tools/call",
            {
                "name": "get_setup_guide",
                "arguments": {"platform": "linux", "edition": setup_edition},
            },
        )
        assert not guide.get("isError", False), guide
        assert "mcp register" in guide["structuredContent"]["markdown"]
        resource = call(
            4, "resources/read", {"uri": f"smaug://setup/linux/{setup_edition}"}
        )
        assert resource["contents"][0]["text"] == guide["structuredContent"]["markdown"]
        status = call(5, "tools/call", {"name": "get_service_status", "arguments": {}})
        assert not status.get("isError", False), status
        assert status["structuredContent"]["status"] == expected_status, status
        if ticker is not None:
            portfolio = call(
                6, "tools/call", {"name": "list_portfolio", "arguments": {}}
            )
            assert not portfolio.get("isError", False), portfolio
            assert any(
                row["ticker"] == ticker
                for row in portfolio["structuredContent"]["items"]
            )
            if expected_status == "ready":
                analysis = call(
                    7,
                    "tools/call",
                    {"name": "get_ticker_analysis", "arguments": {"ticker": ticker}},
                )
                assert not analysis.get("isError", False), analysis
                assert analysis["structuredContent"]["ticker"] == ticker
    finally:
        process.stdin.close()
        try:
            process.wait(timeout=30)
        except subprocess.TimeoutExpired:
            process.terminate()
            process.wait(timeout=15)
        process.stdout.close()
        thread.join(timeout=5)
    assert process.returncode == 0


def smoke(bundle_dir: Path) -> None:
    with tempfile.TemporaryDirectory(prefix="smaug installer smoke ") as directory:
        root = Path(directory)
        os.environ["HOME"] = str(root / "user")
        os.environ["USERPROFILE"] = str(root / "user")
        (root / "user").mkdir()
        for edition in ("backend", "full"):
            bundle = root / f"{edition}-fixture-bundle"
            shutil.copytree(bundle_dir / edition, bundle)
            fixture = Path(__file__).with_name("source_fixture.py").read_text("utf-8")
            compose_path = bundle / "compose.yaml"
            compose_text = (
                compose_path.read_text()
                .replace(
                    "entrypoint: [smaug]",
                    "entrypoint: " + json.dumps(["python", "-c", fixture]),
                )
                .replace(
                    "AUTO_PRICE_REFRESH: ${AUTO_PRICE_REFRESH:-true}",
                    "AUTO_PRICE_REFRESH: 'false'",
                )
            )
            compose_path.write_text(compose_text)
            bundle_manifest = json.loads((bundle / "manifest.json").read_text())
            bundle_manifest["files"]["compose.yaml"] = hashlib.sha256(
                compose_path.read_bytes()
            ).hexdigest()
            (bundle / "manifest.json").write_text(json.dumps(bundle_manifest))
            (bundle / "checksums.txt").write_text(
                "".join(
                    f"{hashlib.sha256(path.read_bytes()).hexdigest()}  {path.name}\n"
                    for path in sorted(bundle.iterdir())
                    if path.name != "checksums.txt"
                )
            )
            guide = (bundle / "SETUP.md").read_text("utf-8")
            assert f"**{edition}**" in guide
            assert "@@EDITION@@" not in guide
            system = {"Linux": "linux", "Darwin": "darwin", "Windows": "windows"}[
                platform.system()
            ]
            arch = (
                "arm64"
                if platform.machine().lower() in {"aarch64", "arm64"}
                else "amd64"
            )
            binary = bundle / (
                f"smaugctl-{system}-{arch}" + (".exe" if system == "windows" else "")
            )
            binary.chmod(0o700)
            install = root / edition
            api, web = port(), port()
            ctl = [str(binary), "--dir", str(install)]
            compose: list[str] = []
            compose_env: dict[str, str] = {}
            try:
                bootstrap = (
                    ["powershell", "-NoProfile", "-File", str(bundle / "install.ps1")]
                    if system == "windows"
                    else ["bash", str(bundle / "install.sh")]
                )
                run(
                    *bootstrap,
                    "--dir",
                    str(install),
                    "--api-port",
                    str(api),
                    "--web-port",
                    str(web),
                )
                config = json.loads((install / "config.json").read_text())
                config["auto_price_refresh"] = False
                (install / "config.json").write_text(json.dumps(config))
                state = json.loads((install / "current.json").read_text())
                release = install / "releases" / state["version"]
                manifest = json.loads((release / "manifest.json").read_text())
                compose = [
                    "docker",
                    "compose",
                    "--project-name",
                    config["project"],
                    "--file",
                    str(release / "compose.yaml"),
                ]
                compose_env = {
                    **os.environ,
                    **{
                        f"SMAUG_{name.upper()}_IMAGE": value
                        for name, value in manifest["images"].items()
                    },
                    "API_PORT": str(api),
                    "WEB_PORT": str(web),
                    "AUTO_PRICE_REFRESH": "false",
                }
                run(*ctl, "check")
                assert request(api, "/ready")["data"] == "prepared"
                assert request(api, "/analysis/PETR4")
                if edition == "full":
                    with urllib.request.urlopen(
                        f"http://127.0.0.1:{web}/ticker/PETR4", timeout=60
                    ) as page:
                        assert b"PETR4" in page.read()
                assert request(api, "/health")["commit"] == manifest["commit"]
                check_gui(ctl, install)
                if edition == "full":
                    # A 200 home page alone can hide an unreachable backend.
                    assert request(web, "/api/data-revision")["revision"]
                assert "roe" in run(*ctl, "cli", "formulas", capture=True)
                run(*ctl, "cli", "doctor", "--help")
                assert request(api, "/portfolio/PETR4", "POST")["ticker"] == "PETR4"
                check_mcp(ctl, expected_status="ready", ticker="PETR4")
                subprocess.run(
                    [*compose, "stop", "api", "frontend"], env=compose_env, check=True
                )
                check_mcp(ctl, expected_status="api_unavailable")
                # Store raw and Bronze evidence through container-local paths.
                subprocess.run(
                    [
                        *compose,
                        "exec",
                        "-T",
                        "mongo",
                        "mongosh",
                        "--quiet",
                        "--eval",
                        'db.getSiblingDB("smaug").installer_smoke.insertOne({evidence:"preserved"})',
                    ],
                    env=compose_env,
                    check=True,
                )
                subprocess.run(
                    [
                        *compose,
                        "run",
                        "--rm",
                        "--no-deps",
                        "-T",
                        "--entrypoint",
                        "python",
                        "cli",
                        "-c",
                        (
                            "from pathlib import Path; "
                            "Path('/data/sources/smoke.txt').write_text('preserved')"
                        ),
                    ],
                    env=compose_env,
                    check=True,
                )
                run(*ctl, "start")
                # Reinstall is idempotent and does not change ports or collect data.
                run(*ctl, "install", "--source", str(bundle))
                run(*ctl, "backup")
                backups = sorted((install / "backups").iterdir())
                assert len(backups) == 1
                backup = backups[0]
                assert (backup / "mongo.archive").stat().st_size > 0
                assert (backup / "sources.tar.gz").stat().st_size > 0
                # Upgrade a real populated legacy schema in a separate DB.
                # Do not require future migrations to support downgrades.
                legacy_db = "smaug_migration_test"
                subprocess.run(
                    [
                        *compose,
                        "exec",
                        "-T",
                        "postgres",
                        "createdb",
                        "-U",
                        "smaug",
                        legacy_db,
                    ],
                    env=compose_env,
                    check=True,
                )
                migration = [
                    *compose,
                    "run",
                    "--rm",
                    "--no-deps",
                    "-T",
                    "-e",
                    f"POSTGRES_URI=postgresql+asyncpg://smaug:smaug@postgres:5432/{legacy_db}",
                    "migrate",
                    "upgrade",
                ]
                subprocess.run([*migration, "0043"], env=compose_env, check=True)
                sql = "INSERT INTO portfolio(ticker, added_at) VALUES ('PETR4', now())"
                subprocess.run(
                    [
                        *compose,
                        "exec",
                        "-T",
                        "postgres",
                        "psql",
                        "-v",
                        "ON_ERROR_STOP=1",
                        "-U",
                        "smaug",
                        "-d",
                        legacy_db,
                        "-c",
                        sql,
                    ],
                    env=compose_env,
                    check=True,
                )
                subprocess.run([*migration, "head"], env=compose_env, check=True)
                result = subprocess.run(
                    [
                        *compose,
                        "exec",
                        "-T",
                        "postgres",
                        "psql",
                        "-At",
                        "-U",
                        "smaug",
                        "-d",
                        legacy_db,
                        "-c",
                        "SELECT ticker FROM portfolio",
                    ],
                    env=compose_env,
                    text=True,
                    capture_output=True,
                    check=True,
                )
                assert result.stdout.strip() == "PETR4"
                # A synthetic release identity tests offline update/restart without
                # asking GitHub for a release that has not been published yet.
                next_bundle = root / (edition + "-next")
                shutil.copytree(bundle, next_bundle)
                next_manifest = json.loads((next_bundle / "manifest.json").read_text())
                next_manifest["commit"] = "b" * 40
                next_manifest["version"] = "2099.01.01-" + "b" * 12
                (next_bundle / "manifest.json").write_text(json.dumps(next_manifest))
                run(*ctl, "update", "--source", str(next_bundle))
                run(*ctl, "check")
                run(*ctl, "edition", "full" if edition == "backend" else "backend")
                run(*ctl, "edition", edition)
                request(api, "/portfolio/PETR4", "DELETE")
                subprocess.run(
                    [
                        *compose,
                        "exec",
                        "-T",
                        "mongo",
                        "mongosh",
                        "--quiet",
                        "--eval",
                        'db.getSiblingDB("smaug").installer_smoke.drop()',
                    ],
                    env=compose_env,
                    check=True,
                )
                subprocess.run(
                    [
                        *compose,
                        "run",
                        "--rm",
                        "--no-deps",
                        "-T",
                        "--entrypoint",
                        "python",
                        "cli",
                        "-c",
                        (
                            "from pathlib import Path; "
                            "Path('/data/sources/smoke.txt').write_text('changed')"
                        ),
                    ],
                    env=compose_env,
                    check=True,
                )
                run(*ctl, "restore", "--backup", str(backup))
                mongo_result = subprocess.run(
                    [
                        *compose,
                        "exec",
                        "-T",
                        "mongo",
                        "mongosh",
                        "--quiet",
                        "--eval",
                        'db.getSiblingDB("smaug").installer_smoke.countDocuments({evidence:"preserved"})',
                    ],
                    env=compose_env,
                    text=True,
                    capture_output=True,
                    check=True,
                )
                assert mongo_result.stdout.strip() == "1"
                run(*ctl, "check")
                assert any(
                    row["ticker"] == "PETR4" for row in request(api, "/portfolio")
                )
                bronze = subprocess.run(
                    [
                        *compose,
                        "run",
                        "--rm",
                        "--no-deps",
                        "-T",
                        "--entrypoint",
                        "python",
                        "cli",
                        "-c",
                        (
                            "from pathlib import Path; "
                            "assert Path('/data/sources/smoke.txt').read_text() "
                            "== 'preserved'"
                        ),
                    ],
                    env=compose_env,
                    check=False,
                )
                assert bronze.returncode == 0
                print(
                    f"PASS: {edition} installation, migration, update, "
                    "source ingestion, analysis, edition switch, full backup, "
                    "restore and MCP stdio",
                    flush=True,
                )
            finally:
                # Only ephemeral projects created here can be removed, even when
                # install fails before this test has assembled a Compose command.
                config_path = install / "config.json"
                current_path = install / "current.json"
                if config_path.exists() and current_path.exists():
                    config = json.loads(config_path.read_text())
                    state = json.loads(current_path.read_text())
                    release = install / "releases" / state["version"]
                    manifest = json.loads((release / "manifest.json").read_text())
                    compose_env.update(
                        {
                            **os.environ,
                            **{
                                f"SMAUG_{name.upper()}_IMAGE": value
                                for name, value in manifest["images"].items()
                            },
                            "API_PORT": str(api),
                            "WEB_PORT": str(web),
                        }
                    )
                    subprocess.run(
                        [
                            "docker",
                            "compose",
                            "--project-name",
                            config["project"],
                            "--file",
                            str(release / "compose.yaml"),
                            "--profile",
                            "full",
                            "--profile",
                            "tools",
                            "down",
                            "--volumes",
                            "--remove-orphans",
                        ],
                        env=compose_env,
                        check=True,
                    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bundles", type=Path, required=True)
    args = parser.parse_args()
    smoke(args.bundles.resolve())


if __name__ == "__main__":
    main()
