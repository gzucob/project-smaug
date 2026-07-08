"""Entry shim: ``python -m smaug.analyze`` runs the analyze command."""

from __future__ import annotations

import typer

from smaug.entrypoints.cli import analyze

if __name__ == "__main__":
    typer.run(analyze)
