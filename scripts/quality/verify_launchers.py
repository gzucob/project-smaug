"""Exercise launcher argument forwarding and checksum rejection on this platform."""

import hashlib
import json
import os
import shutil
import subprocess
from pathlib import Path
from tempfile import TemporaryDirectory

INSTALLER = Path(__file__).resolve().parents[2] / "installer"


def main() -> None:
    """Run the platform launcher against a disposable stand-in for the manager."""
    system = subprocess.check_output(["go", "env", "GOOS"], text=True).strip()
    arch = subprocess.check_output(["go", "env", "GOARCH"], text=True).strip()
    windows = system == "windows"
    name = "install.ps1" if windows else "install.sh"
    # Windows PowerShell needs its own module path when a PS 7 runner starts it.
    environment = {
        key: value
        for key, value in os.environ.items()
        if not windows or key.upper() != "PSMODULEPATH"
    }
    with TemporaryDirectory(prefix="smaug launcher ") as directory:
        bundle = Path(directory)
        entrypoint = bundle / name
        shutil.copyfile(INSTALLER / name, entrypoint)
        if windows:
            command = [
                "powershell",
                "-NoProfile",
                "-ExecutionPolicy",
                "Bypass",
                "-File",
            ]
        else:
            subprocess.run(["bash", "-n", entrypoint], check=True)
            command = ["bash"]
        binary = bundle / f"smaugctl-{system}-{arch}{'.exe' if windows else ''}"
        source = bundle / "fixture.go"
        source.write_text(
            'package main\nimport ("encoding/json"; "os")\n'
            "func main() { json.NewEncoder(os.Stdout).Encode(os.Args[1:]) }\n",
            encoding="utf-8",
        )
        subprocess.run(["go", "build", "-o", binary, source], check=True)
        edition = bundle / "edition.txt"
        edition.write_text("mcp\n", encoding="utf-8")
        checksums = bundle / "checksums.txt"
        digest = hashlib.sha256(binary.read_bytes()).hexdigest()
        checksums.write_text(f"{digest}  {binary.name}\n", encoding="ascii")
        target = bundle / "example install"
        invocation = [*command, entrypoint, "--dir", str(target), "--help"]
        result = subprocess.run(
            invocation,
            check=False,
            capture_output=True,
            text=True,
            encoding="utf-8",
            env=environment,
        )
        assert result.returncode == 0, result.stderr
        forwarded = json.loads(result.stdout.splitlines()[-1])
        assert len(forwarded) == 7, forwarded
        assert forwarded[0] == "--dir", forwarded
        assert Path(forwarded[1]).resolve() == target.resolve(), forwarded
        assert forwarded[2:5] == ["mcp", "setup", "--source"], forwarded
        assert Path(forwarded[5]).resolve() == bundle.resolve(), forwarded
        assert forwarded[6] == "--help", forwarded
        os.chmod(binary, 0o700)
        with binary.open("ab") as handle:
            handle.write(b"modified")
        refused = subprocess.run(
            invocation,
            check=False,
            capture_output=True,
            text=True,
            encoding="utf-8",
            env=environment,
        )
        assert refused.returncode != 0
        assert "checksum" in refused.stderr.lower(), refused.stderr


if __name__ == "__main__":
    main()
