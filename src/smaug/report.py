"""Entry point shim: ``python -m smaug.report`` (plan §6).

Thin trigger for the completeness report; wiring lives in ``entrypoints.cli``.
"""

from __future__ import annotations

import typer

from smaug.entrypoints.cli import report

if __name__ == "__main__":
    typer.run(report)
