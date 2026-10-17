"""One packaged setup guide for MCP, README and offline installer bundles."""

import re
from importlib.resources import files
from typing import Literal

type SetupPlatform = Literal["all", "linux", "macos", "windows"]
type SetupEdition = Literal["backend", "full", "mcp"]

SETUP_GUIDE_URI = "smaug://setup"
SETUP_GUIDE_HINT = (
    "Use get_service_status to inspect the API and get_setup_guide "
    "(or smaug://setup) for installation, smaugctl start/check and "
    "MCP_API_BASE_URL instructions."
)
README_START = "<!-- BEGIN SMAUG SETUP GUIDE -->"
README_END = "<!-- END SMAUG SETUP GUIDE -->"


def render_setup_guide(
    platform: SetupPlatform = "all", edition: SetupEdition = "full"
) -> str:
    """Select platform instructions locally, without HTTP or command execution."""
    if platform not in ("all", "linux", "macos", "windows"):
        raise ValueError("Unsupported setup platform")
    if edition not in ("backend", "full", "mcp"):
        raise ValueError("Unsupported setup edition")
    sections = _guide_sections()
    if edition == "mcp":
        return _select_platform(sections["mcp"], platform) + "\n"
    selected = (
        text.strip()
        for name, text in sections.items()
        if name not in {"linux", "macos", "windows"} or platform in {"all", name}
    )
    return (
        _select_platform("\n\n".join(selected), platform).replace(
            "@@EDITION@@", edition
        )
        + "\n"
    )


def _guide_sections() -> dict[str, str]:
    source = files("smaug.shared").joinpath("setup_guide.md").read_text("utf-8")
    parts = re.split(r"^<!-- section:(\w+) -->\n", source, flags=re.MULTILINE)
    sections = dict(zip(parts[1::2], parts[2::2], strict=True))
    if set(sections) != {
        "common",
        "linux",
        "macos",
        "windows",
        "operations",
        "data",
        "maintenance",
        "mcp",
    }:
        raise ValueError("Invalid setup guide sections")
    return sections


def _select_platform(source: str, platform: SetupPlatform) -> str:
    def select(match: re.Match[str]) -> str:
        return match[2].strip() if platform in ("all", match[1]) else ""

    selected = re.sub(
        r"<!-- platform:(linux|macos|windows) -->\n(.*?)<!-- /platform -->",
        select,
        source,
        flags=re.DOTALL,
    )
    return re.sub(r"\n{3,}", "\n\n", selected).strip()
