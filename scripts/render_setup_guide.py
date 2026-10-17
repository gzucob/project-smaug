"""Refresh the README's generated setup guide, or verify it with --check."""

import argparse
from pathlib import Path

from smaug.shared.mcp_setup import README_END, README_START, render_setup_guide


def render_readme(readme: str) -> str:
    """Replace only the delimited setup guide; preserve the surrounding README."""
    prefix, _, rest = readme.partition(README_START)
    _, marker, suffix = rest.partition(README_END)
    if not marker:
        raise ValueError("README setup guide markers are missing")
    return (
        prefix
        + README_START
        + "\n\n"
        + render_setup_guide()
        + "\n"
        + README_END
        + suffix
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    path = Path(__file__).resolve().parents[1] / "README.md"
    current = path.read_text("utf-8")
    rendered = render_readme(current)
    if args.check:
        if current != rendered:
            parser.exit(1, "Run uv run python scripts/render_setup_guide.py\n")
    else:
        path.write_text(rendered, encoding="utf-8")


if __name__ == "__main__":
    main()
