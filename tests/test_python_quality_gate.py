"""A failed suite or coverage check fails closed and cleans its resources."""

import subprocess
import sys
from pathlib import Path

import pytest
from scripts.quality import python_gate


@pytest.mark.parametrize("failure", [0, 1])
def test_quality_gate_always_cleans_its_disposable_databases(
    failure: int, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[list[str]] = []

    def run(
        command: list[str], *, cwd: Path, check: bool
    ) -> subprocess.CompletedProcess[str]:
        assert cwd == python_gate.ROOT
        calls.append(command)
        if "run" in command:
            assert check
            assert command[-2] == "-ec"
            assert command[-1].index("pytest") < command[-1].index("python_coverage")
            if failure:
                raise subprocess.CalledProcessError(failure, command)
        return subprocess.CompletedProcess(command, 0)

    monkeypatch.setattr(subprocess, "run", run)
    monkeypatch.setattr(sys, "argv", ["python_gate", "--coverage"])

    assert python_gate.main() == failure
    assert len(calls) == 3
    assert calls[-1][-3:] == ["down", "--volumes", "--remove-orphans"]
    assert calls[0][3].startswith("smaug-quality-")
    assert calls[0][:6] == calls[-1][:6]
