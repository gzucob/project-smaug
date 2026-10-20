"""Offline onboarding and read-only service diagnostics over MCP contracts."""

from pathlib import Path

import httpx
import pytest
from mcp import Client
from mcp.types import TextResourceContents
from pydantic import JsonValue

from smaug.entrypoints.mcp import create_server
from smaug.shared.config import Settings
from smaug.shared.mcp_setup import (
    README_END,
    README_START,
    SetupEdition,
    SetupPlatform,
    render_setup_guide,
)


@pytest.mark.parametrize("edition", ["backend", "full"])
@pytest.mark.parametrize("platform", ["all", "linux", "macos", "windows"])
async def test_setup_tool_and_resources_work_without_http(
    platform: SetupPlatform, edition: SetupEdition
) -> None:
    def offline(request: httpx.Request) -> httpx.Response:
        pytest.fail(f"Setup made an HTTP request: {request.url}")

    async with Client(
        create_server(Settings(), transport=httpx.MockTransport(offline))
    ) as client:
        result = await client.call_tool(
            "get_setup_guide", {"platform": platform, "edition": edition}
        )
        assert not result.is_error
        assert result.structured_content is not None
        content = result.structured_content
        assert content["platform"] == platform
        assert content["edition"] == edition
        guide = content["markdown"]
        assert f"**{edition}**" in guide
        assert "@@EDITION@@" not in guide
        for name, heading in (
            ("linux", "### Linux e WSL"),
            ("macos", "### macOS"),
            ("windows", "### Windows"),
        ):
            assert (heading in guide) == (platform in ("all", name))
        assert "smaug upgrade" in guide
        assert "smaug installer" in guide
        assert "smaug mcp register --client codex" in guide
        assert "cli ingest" not in guide
        assert "smaug-mcp-VERSAO" not in guide
        resource = await client.read_resource(content["resource_uri"])
        assert isinstance(resource.contents[0], TextResourceContents)
        assert resource.contents[0].mime_type == "text/markdown"
        assert resource.contents[0].text == guide
        default = await client.read_resource("smaug://setup")
        assert isinstance(default.contents[0], TextResourceContents)
        assert default.contents[0].text == render_setup_guide()
        assert "smaug://setup" in {
            str(resource.uri) for resource in (await client.list_resources()).resources
        }
        assert (await client.list_resource_templates()).resource_templates


async def test_setup_rejects_unsupported_options() -> None:
    async with Client(create_server(Settings())) as client:
        for args in (
            {"platform": "freebsd"},
            {"edition": "desktop"},
            {"edition": "mcp"},
        ):
            result = await client.call_tool("get_setup_guide", args)
            assert result.is_error


def test_documentation_uses_the_packaged_setup_guide() -> None:
    docs = Path(__file__).resolve().parents[1] / "docs"
    installation, heading, mcp = render_setup_guide().partition("\n## MCP\n")
    assert heading
    for name, expected in (
        ("installation.md", installation),
        ("mcp.md", mcp),
    ):
        document = (docs / name).read_text("utf-8")
        generated = document.split(README_START, 1)[1].split(README_END, 1)[0]
        assert generated.strip() == expected.strip()


@pytest.mark.parametrize(
    ("code", "ready", "mongo", "schema", "data", "expected"),
    [
        (200, True, "ok", "ok", "prepared", "ready"),
        (200, True, "ok", "ok", "empty", "ready_empty"),
        (503, False, "ok", "migration_required", "unavailable", "migration_required"),
        (503, False, "unavailable", "ok", "empty", "dependencies_unavailable"),
        (503, False, "ok", "unavailable", "unavailable", "dependencies_unavailable"),
        (200, False, "ok", "ok", "empty", "invalid_api_response"),
        (503, True, "ok", "ok", "empty", "invalid_api_response"),
        (200, True, "ok", "migration_required", "empty", "invalid_api_response"),
    ],
)
async def test_status_preserves_health_and_readiness(
    code: int, ready: bool, mongo: str, schema: str, data: str, expected: str
) -> None:
    calls: list[str] = []
    health = {"status": "ok", "commit": "example-commit"}
    readiness = {
        "ready": ready,
        "mongo": mongo,
        "postgres": "ok",
        "schema": schema,
        "data": data,
    }

    def handle(request: httpx.Request) -> httpx.Response:
        assert request.method == "GET"
        assert request.extensions["timeout"]["read"] == 4
        calls.append(str(request.url))
        if request.url.path == "/prefix/health":
            return httpx.Response(200, json=health)
        assert request.url.path == "/prefix/ready"
        return httpx.Response(code, json=readiness)

    settings = Settings(
        mcp_api_base_url="http://localhost:8765/prefix", mcp_api_timeout_seconds=4
    )
    async with Client(
        create_server(settings, transport=httpx.MockTransport(handle))
    ) as client:
        result = await client.call_tool("get_service_status", {})
        assert not result.is_error
        body = result.structured_content
        assert body is not None
        assert body["status"] == expected
        assert body["health"] == health
        assert body["readiness"] == readiness
        assert body["readiness_http_status"] == code
        assert body["setup_guide_uri"] == "smaug://setup"
        assert body["next_steps"]
    assert calls == [
        "http://localhost:8765/prefix/health",
        "http://localhost:8765/prefix/ready",
    ]


@pytest.mark.parametrize("failed_path", ["health", "ready"])
@pytest.mark.parametrize(
    ("failure", "expected"),
    [
        ("connection", "api_unavailable"),
        ("timeout", "api_timeout"),
        ("redirect", "api_http_error"),
        ("http500", "api_http_error"),
        ("html", "invalid_api_response"),
        ("array", "invalid_api_response"),
        ("missing_fields", "invalid_api_response"),
    ],
)
async def test_status_reports_failure_and_keeps_partial_health(
    failed_path: str, failure: str, expected: str
) -> None:
    calls: list[str] = []
    health = {"status": "ok", "commit": "test"}

    def handle(request: httpx.Request) -> httpx.Response:
        calls.append(request.url.path)
        if request.url.path != f"/{failed_path}":
            assert request.url.path == "/health"
            return httpx.Response(200, json=health)
        if failure == "connection":
            raise httpx.ConnectError("offline", request=request)
        if failure == "timeout":
            raise httpx.ReadTimeout("slow", request=request)
        if failure == "redirect":
            return httpx.Response(302, headers={"location": "https://example.com"})
        if failure == "http500":
            return httpx.Response(500)
        if failure == "html":
            return httpx.Response(200, text="<html>unexpected</html>")
        return httpx.Response(200, json=[] if failure == "array" else {})

    async with Client(
        create_server(Settings(), transport=httpx.MockTransport(handle))
    ) as client:
        result = await client.call_tool("get_service_status", {})
        assert not result.is_error
        body: dict[str, JsonValue] | None = result.structured_content
        assert body is not None
        assert body["status"] == expected
        assert body["error"]
        assert "get_setup_guide" in str(body["next_steps"])
        if failed_path == "ready":
            assert body["health"] == health
    assert calls == (["/health"] if failed_path == "health" else ["/health", "/ready"])
