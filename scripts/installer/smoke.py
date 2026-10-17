"""Exercise disposable backend/full installations; never connect to checkout DBs.

Requires prebuilt bundles and Docker. Removes only the Compose projects whose
configuration was created inside this invocation's temporary directory.
"""

from __future__ import annotations

import argparse
import json
import os
import platform
import queue
import shutil
import socket
import subprocess
import tempfile
import threading
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


def check_mcp(
    command: list[str], *, expected_status: str, ticker: str | None = None
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
                "arguments": {"platform": "linux", "edition": "backend"},
            },
        )
        assert not guide.get("isError", False), guide
        assert "mcp register" in guide["structuredContent"]["markdown"]
        resource = call(4, "resources/read", {"uri": "smaug://setup/linux/backend"})
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
        for edition in ("backend", "full"):
            bundle = bundle_dir / edition
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
                assert request(api, "/ready")["data"] == "empty"
                assert request(api, "/health")["commit"] == manifest["commit"]
                if edition == "full":
                    # A 200 home page alone can hide an unreachable backend.
                    assert request(web, "/api/data-revision")["revision"]
                assert "roe" in run(*ctl, "cli", "formulas", capture=True)
                run(*ctl, "cli", "doctor", "--help")
                assert request(api, "/portfolio/PETR4", "POST")["ticker"] == "PETR4"
                check_mcp(ctl, expected_status="ready_empty", ticker="PETR4")
                connector_ctl: list[str] | None = None
                if edition == "backend":
                    connector_bundle = bundle_dir / "mcp"
                    connector_guide = (connector_bundle / "SETUP.md").read_text("utf-8")
                    assert "Pacote separado" in connector_guide
                    assert "@@EDITION@@" not in connector_guide
                    connector_binary = connector_bundle / binary.name
                    connector_binary.chmod(0o700)
                    connector_root = root / "standalone MCP"
                    connector_ctl = [
                        str(connector_binary),
                        "--dir",
                        str(connector_root),
                    ]
                    bootstrap_mcp = ["bash", str(connector_bundle / "install.sh")]
                    run(
                        *bootstrap_mcp,
                        "--dir",
                        str(connector_root),
                        "--api-base-url",
                        f"http://127.0.0.1:{api}",
                    )
                    assert not (connector_root / "config.json").exists()
                    check_mcp(
                        connector_ctl, expected_status="ready_empty", ticker="PETR4"
                    )
                    next_connector = root / "mcp-next"
                    shutil.copytree(connector_bundle, next_connector)
                    next_connector_manifest = json.loads(
                        (next_connector / "manifest.json").read_text()
                    )
                    next_connector_manifest["commit"] = "c" * 40
                    next_connector_manifest["version"] = "2099.01.01-" + "c" * 12
                    (next_connector / "manifest.json").write_text(
                        json.dumps(next_connector_manifest)
                    )
                    run(
                        *connector_ctl, "mcp", "update", "--source", str(next_connector)
                    )
                    saved_connector = json.loads(
                        (connector_root / "mcp.json").read_text()
                    )
                    assert saved_connector["api_base_url"] == f"http://127.0.0.1:{api}"
                    check_mcp(
                        connector_ctl, expected_status="ready_empty", ticker="PETR4"
                    )
                subprocess.run(
                    [*compose, "stop", "api", "frontend"], env=compose_env, check=True
                )
                check_mcp(ctl, expected_status="api_unavailable")
                if connector_ctl is not None:
                    check_mcp(connector_ctl, expected_status="api_unavailable")
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
                    "edition switch, full backup, restore and installed MCP stdio",
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
