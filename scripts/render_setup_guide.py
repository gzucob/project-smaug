"""Refresh the installation and MCP guides, or verify them with --check."""

import argparse
from pathlib import Path

from smaug.shared.mcp_setup import README_END, README_START, render_setup_guide


def render_document(document: str, guide: str) -> str:
    """Replace the delimited guide and preserve the surrounding document."""
    prefix, start, rest = document.partition(README_START)
    _, marker, suffix = rest.partition(README_END)
    if not start or not marker:
        raise ValueError("Setup guide markers are missing")
    return prefix + README_START + "\n\n" + guide + "\n" + README_END + suffix


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    docs = Path(__file__).resolve().parents[1] / "docs"
    installation, heading, mcp = render_setup_guide().partition("\n## MCP\n")
    if not heading:
        raise ValueError("MCP setup guide section is missing")
    for name, guide in (
        ("installation.md", installation.rstrip() + "\n"),
        ("mcp.md", mcp.lstrip()),
    ):
        path = docs / name
        current = path.read_text("utf-8")
        rendered = render_document(current, guide)
        if args.check:
            if current != rendered:
                parser.exit(1, "Run uv run python scripts/render_setup_guide.py\n")
        else:
            path.write_text(rendered, encoding="utf-8")


if __name__ == "__main__":
    main()
