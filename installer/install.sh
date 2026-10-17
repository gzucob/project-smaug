#!/usr/bin/env bash
# Run from the extracted, checksummed release; no host language runtime needed.
set -euo pipefail
bundle=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
case "$(uname -s)" in
  Linux) system=linux ;;
  Darwin) system=darwin ;;
  *) echo 'Use install.ps1 on Windows.' >&2; exit 1 ;;
esac
case "$(uname -m)" in
  x86_64|amd64) arch=amd64 ;;
  arm64|aarch64) arch=arm64 ;;
  *) echo 'Unsupported architecture; use amd64 or arm64.' >&2; exit 1 ;;
esac
binary="$bundle/smaugctl-$system-$arch"
# Verify every packaged file before executing a downloaded binary.
if command -v sha256sum >/dev/null 2>&1; then
  (cd "$bundle" && sha256sum --check checksums.txt)
else
  (cd "$bundle" && shasum -a 256 --check checksums.txt)
fi
chmod u+x "$binary"
# Optional --dir DIRECTORY must precede install; remaining flags belong to it.
if [[ ${1:-} == --dir ]]; then
  target=$2; shift 2
  exec "$binary" --dir "$target" install --source "$bundle" "$@"
fi
exec "$binary" install --source "$bundle" "$@"
