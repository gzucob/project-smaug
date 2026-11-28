#!/usr/bin/env bash
# Restore the release channel without moving or replacing published tags.
set -euo pipefail

repo="repos/$GITHUB_REPOSITORY"
latest=$(gh api "$repo/releases/latest" --jq .tag_name)
if [[ "$latest" == "$VERSION" ]]; then
  gh release edit "$PREVIOUS" --repo "$GITHUB_REPOSITORY" --latest
elif [[ "$latest" != "$PREVIOUS" ]]; then
  echo "Refusing to move a release channel that another publication changed." >&2
  exit 1
fi

draft=$(gh release view "$VERSION" --repo "$GITHUB_REPOSITORY" --json isDraft --jq .isDraft)
if [[ "$draft" == false ]]; then
  gh release edit "$VERSION" --repo "$GITHUB_REPOSITORY" --prerelease --latest=false
fi
test "$(gh api "$repo/releases/latest" --jq .tag_name)" = "$PREVIOUS"
