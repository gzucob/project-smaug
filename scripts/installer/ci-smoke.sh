#!/usr/bin/env bash
# CI-only registry and images; smoke.py removes only its disposable databases.
set -euo pipefail
registry="smaug-installer-ci-registry-$RANDOM-$RANDOM"
if docker container inspect "$registry" >/dev/null 2>&1; then
  echo "Refusing to replace an existing container: $registry" >&2
  exit 1
fi
trap 'docker rm -f "$registry" >/dev/null 2>&1 || true' EXIT
docker run -d --name "$registry" -p 127.0.0.1:5501:5000 registry:2
docker build --build-arg "APPLICATION_COMMIT=$GITHUB_SHA" -t 127.0.0.1:5501/smaug-backend:ci .
docker build -t 127.0.0.1:5501/smaug-frontend:ci frontend
docker push 127.0.0.1:5501/smaug-backend:ci
docker push 127.0.0.1:5501/smaug-frontend:ci
backend=$(docker image inspect 127.0.0.1:5501/smaug-backend:ci --format '{{index .RepoDigests 0}}')
frontend=$(docker image inspect 127.0.0.1:5501/smaug-frontend:ci --format '{{index .RepoDigests 0}}')
version="$(date -u +%Y.%m.%d)-${GITHUB_SHA:0:12}"
uv run python scripts/installer/build_release.py --commit "$GITHUB_SHA" --version "$version" --backend "$backend" --frontend "$frontend"
uv run python scripts/installer/smoke.py --bundles dist/installers
