"""Entry point shim: ``python -m smaug.ingest`` (plan §5).

Thin trigger for the batch collector; all wiring lives in ``entrypoints.cli``.
"""

from __future__ import annotations

import typer

from smaug.entrypoints.cli import ingest

if __name__ == "__main__":
    typer.run(ingest)
