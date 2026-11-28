"""Promotion metadata must match the merged tree and approved intent."""

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "scripts/quality/select_promotion.sh"
ROLLBACK = SCRIPT.with_name("restore_release.sh")


@pytest.mark.skipif(
    sys.platform == "win32" or not shutil.which("jq"),
    reason="The promotion workflow runs in Linux with Bash and jq",
)
@pytest.mark.parametrize(
    ("mode", "wrong_tree", "expected"),
    [("release", False, 0), ("no-release", False, 0), ("release", True, 1)],
)
def test_publisher_matches_the_approved_candidate(
    tmp_path: Path, mode: str, wrong_tree: bool, expected: int
) -> None:
    def git(*args: str) -> str:
        return subprocess.check_output(
            [
                "git",
                "-C",
                str(tmp_path),
                "-c",
                "user.name=Test",
                "-c",
                "user.email=test@example.test",
                *args,
            ],
            text=True,
        ).strip()

    git("init", "--initial-branch=main")
    git("commit", "--allow-empty", "-m", "base")
    base = git("rev-parse", "HEAD")
    git("switch", "-c", "develop")
    git("commit", "--allow-empty", "-m", "candidate")
    head = git("rev-parse", "HEAD")
    git("switch", "main")
    git("merge", "--no-ff", "-m", "promote", "develop")
    merge = git("rev-parse", "HEAD")
    repo = "test/project"
    reason = (
        "No-release reason: update repository tooling" if mode == "no-release" else ""
    )
    pr = {
        "number": 5,
        "merged_at": "2026-11-26",
        "merge_commit_sha": merge,
        "base": {"ref": "main"},
        "head": {"ref": "develop", "sha": head, "repo": {"full_name": repo}},
        "labels": [{"name": "no-release"}] if reason else [],
        "body": reason,
    }
    candidate = {
        "mode": mode,
        "base": base,
        "head": head,
        "tree": "bad" if wrong_tree else git("rev-parse", "HEAD^{tree}"),
        "tested": merge,
        "version": f"2026.11.26-{merge[:12]}",
        "reason": reason,
        "run": 12,
        "attempt": 1,
        "pr": 5,
    }
    run = {
        "head_repository": {"full_name": repo},
        "head_sha": head,
        "head_branch": "develop",
        "run_number": 3,
        "conclusion": "success",
        "id": 12,
        "run_attempt": 1,
    }
    fake = tmp_path / "gh"
    fake.write_text(
        f"#!{sys.executable}\n"
        "import json, os, sys\n"
        "from pathlib import Path\n"
        "if sys.argv[1] == 'run':\n"
        "    out = Path(sys.argv[sys.argv.index('--dir') + 1]) / 'promotion.json'\n"
        "    out.write_text(os.environ['CANDIDATE'])\n"
        "elif '/commits/' in sys.argv[-1]:\n"
        "    print(os.environ['PR'])\n"
        "else:\n"
        "    print(os.environ['RUN'])\n",
        encoding="utf-8",
    )
    fake.chmod(0o700)
    output = tmp_path / "output"
    result = subprocess.run(
        ["bash", str(SCRIPT)],
        cwd=tmp_path,
        check=False,
        env={
            **os.environ,
            "PATH": f"{tmp_path}{os.pathsep}{os.environ['PATH']}",
            "GITHUB_REPOSITORY": repo,
            "GITHUB_SHA": merge,
            "GITHUB_OUTPUT": str(output),
            "RUNNER_TEMP": str(tmp_path),
            "CANDIDATE": json.dumps(candidate),
            "PR": json.dumps([pr]),
            "RUN": json.dumps({"workflow_runs": [run]}),
        },
        capture_output=True,
        text=True,
    )
    assert result.returncode == expected, result.stderr
    if not expected:
        assert f"mode={mode}" in output.read_text(encoding="utf-8")
    else:
        assert not output.exists()


@pytest.mark.skipif(sys.platform == "win32", reason="The release job runs in Bash")
@pytest.mark.parametrize(
    ("latest", "draft", "channel", "edits"),
    [
        ("candidate", "false", "previous", ["previous", "candidate"]),
        ("previous", "true", "previous", []),
        ("newer", "false", "newer", []),
    ],
)
def test_rollback_preserves_the_previous_channel(
    tmp_path: Path, latest: str, draft: str, channel: str, edits: list[str]
) -> None:
    state = tmp_path / "latest"
    state.write_text(latest, encoding="utf-8")
    fake = tmp_path / "gh"
    fake.write_text(
        f"#!{sys.executable}\n"
        "import os, sys\n"
        "from pathlib import Path\n"
        "state = Path(os.environ['CHANNEL'])\n"
        "if sys.argv[1] == 'api':\n"
        "    print(state.read_text())\n"
        "elif sys.argv[2] == 'view':\n"
        "    print(os.environ['DRAFT'])\n"
        "else:\n"
        "    with state.with_suffix('.edits').open('a') as log:\n"
        "        log.write(sys.argv[3] + '\\n')\n"
        "    if '--latest' in sys.argv[4:]:\n"
        "        state.write_text(sys.argv[3])\n",
        encoding="utf-8",
    )
    fake.chmod(0o700)
    result = subprocess.run(
        ["bash", str(ROLLBACK)],
        cwd=tmp_path,
        check=False,
        env={
            **os.environ,
            "PATH": f"{tmp_path}{os.pathsep}{os.environ['PATH']}",
            "GITHUB_REPOSITORY": "test/project",
            "VERSION": "candidate",
            "PREVIOUS": "previous",
            "CHANNEL": str(state),
            "DRAFT": draft,
        },
        capture_output=True,
        text=True,
    )
    assert result.returncode == (latest == "newer"), result.stderr
    assert state.read_text(encoding="utf-8") == channel
    log = state.with_suffix(".edits")
    assert (
        log.read_text(encoding="utf-8").splitlines() if log.exists() else []
    ) == edits
