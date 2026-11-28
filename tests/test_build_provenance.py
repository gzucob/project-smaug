"""Published build provenance overrides the development checkout."""

import subprocess
from unittest.mock import Mock

import pytest

from smaug.shared import build


def test_packaged_commit_is_used_without_calling_git(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("SMAUG_APPLICATION_COMMIT", "candidate")
    git = Mock(side_effect=AssertionError("git called in a packaged installation"))
    monkeypatch.setattr(build.subprocess, "run", git)

    assert build.application_commit() == "candidate"
    git.assert_not_called()


@pytest.mark.parametrize(
    ("outputs", "expected"),
    [
        (["abcdef\n", ""], "abcdef"),
        (["abcdef\n", " M README.md\n"], "abcdef-dirty"),
        (["\n", ""], "unknown"),
    ],
)
def test_checkout_commit_distinguishes_clean_dirty_and_unavailable(
    outputs: list[str], expected: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("SMAUG_APPLICATION_COMMIT", raising=False)
    git = Mock(
        side_effect=[
            subprocess.CompletedProcess(["git"], 0, value) for value in outputs
        ]
    )
    monkeypatch.setattr(build.subprocess, "run", git)

    assert build.application_commit() == expected
    assert git.call_count == 2


def test_checkout_works_without_git(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("SMAUG_APPLICATION_COMMIT", raising=False)
    monkeypatch.setattr(build.subprocess, "run", Mock(side_effect=FileNotFoundError))
    assert build.application_commit() == "unknown"
