"""Resolve build provenance at the infrastructure boundary."""

from __future__ import annotations

import os
import subprocess
from pathlib import Path


def application_commit() -> str:
    """Return the deployed commit, with a local-checkout fallback."""
    configured = os.getenv("SMAUG_APPLICATION_COMMIT")
    if configured:
        return configured
    try:
        project_root = Path(__file__).resolve().parents[3]
        result = subprocess.run(
            ["git", "-C", str(project_root), "rev-parse", "HEAD"],
            check=True,
            capture_output=True,
            text=True,
            timeout=2,
        )
        status = subprocess.run(
            ["git", "-C", str(project_root), "status", "--porcelain"],
            check=True,
            capture_output=True,
            text=True,
            timeout=2,
        )
    except (OSError, subprocess.SubprocessError):
        return "unknown"
    commit = result.stdout.strip()
    if not commit:
        return "unknown"
    return f"{commit}-dirty" if status.stdout.strip() else commit
