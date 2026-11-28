#!/usr/bin/env bash
# Stage the validated candidate and keep exact Pages bundles for activation and rollback.
set -euo pipefail

repo="repos/$GITHUB_REPOSITORY"
stage=$(mktemp -d "$RUNNER_TEMP/smaug-publication.XXXXXXXX")
trap 'rm -rf -- "$stage"' EXIT
latest=$(gh api "$repo/releases/latest")
previous=$(jq -er '.tag_name' <<< "$latest")
previous_sha=$(jq -er '.target_commitish | select(test("^[a-f0-9]{40}$"))' <<< "$latest")
site_url=$(gh api "$repo/pages" --jq '.html_url')
[[ "$site_url" == https://* ]]
echo "previous=$previous" >> "$GITHUB_OUTPUT"
echo "site_url=$site_url" >> "$GITHUB_OUTPUT"

if [[ "$previous" == "$VERSION" && "$previous_sha" == "$GITHUB_SHA" ]]; then
  curl -fsSL --retry 3 "${site_url%/}/release.json" |
    jq -e --arg version "$VERSION" '.version == $version' >/dev/null
  echo 'published=true' >> "$GITHUB_OUTPUT"
  exit 0
fi
git merge-base --is-ancestor "$previous_sha" "$GITHUB_SHA"
[[ "$previous_sha" != "$GITHUB_SHA" ]]

if jq -e 'any(.assets[]; .name == "smaug-pages.tar.gz")' <<< "$latest" >/dev/null; then
  checksum=$(jq -er '.assets[] | select(.name == "smaug-pages.tar.gz") |
    .digest | select(test("^sha256:[a-f0-9]{64}$"))' <<< "$latest")
  gh release download "$previous" --pattern smaug-pages.tar.gz --dir "$stage"
  printf '%s  %s\n' "${checksum#sha256:}" "$stage/smaug-pages.tar.gz" | sha256sum --check
  mkdir -p "$RUNNER_TEMP/smaug-pages-previous"
  tar -xzf "$stage/smaug-pages.tar.gz" -C "$RUNNER_TEMP/smaug-pages-previous"
else
  # Bootstrap the rollback bundle from the deployed Pages revision and its published data.
  page_version=$(curl -fsSL --retry 3 "${site_url%/}/release.json" | jq -er .version)
  [[ "$page_version" == "$previous" ]]
  deployed=''
  while IFS=$'\t' read -r id sha; do
    if gh api "$repo/deployments/$id/statuses" |
      jq -e '.[0].state == "success"' >/dev/null; then
      deployed="$sha"
      break
    fi
  done < <(gh api "$repo/deployments?environment=github-pages&per_page=30" |
    jq -r '.[] | [.id, .sha] | @tsv')
  [[ "$deployed" =~ ^[a-f0-9]{40}$ ]]
  git merge-base --is-ancestor "$deployed" "$GITHUB_SHA"
  git archive "$deployed" website | tar -x -C "$stage"
  (
    cd "$stage/website"
    npm ci
    npm run prepare:release
    jq -e --arg version "$previous" '.version == $version' public/release.json >/dev/null
    npm run build
    mv dist "$RUNNER_TEMP/smaug-pages-previous"
  )
fi
jq -e --arg version "$previous" '.version == $version' \
  "$RUNNER_TEMP/smaug-pages-previous/release.json" >/dev/null

release_record() {
  gh api --paginate "$repo/releases?per_page=100" |
    jq -sc --arg version "$VERSION" 'add | map(select(.tag_name == $version)) | first // empty'
}

existing=$(release_record)
if [[ -n "$existing" ]]; then
  jq -e --arg sha "$GITHUB_SHA" '.draft == true and .target_commitish == $sha' \
    <<< "$existing" >/dev/null
else
  tested=$(jq -er .tested "$CANDIDATE")
  cat > "$stage/release-notes.md" <<EOF
Native Smaug installers for Windows, Linux and macOS. Validated candidate: $tested.

Open the graphical installer for your system: .exe on Windows, .dmg on macOS, or .deb on Ubuntu/Debian. Choose Full or Backend in the browser interface and click Install. Docker Desktop must be installed and running. The installer imports the bundled public CVM/B3 snapshot and downloads any missing Docker images.

Use the installed Smaug shortcut to start, stop, update, or connect MCP without terminal commands. CLI commands remain available for automation.

The ZIP/tar runtime archives are for upgrades. Use a native installer for a fresh installation.
EOF
  gh release create "$VERSION" --draft --target "$GITHUB_SHA" \
    --title "Smaug $VERSION" --notes-file "$stage/release-notes.md"
fi
draft_id=$(release_record | jq -er 'select(.draft == true) | .id')

upload_verified() (
  cd "$1"
  shopt -s nullglob
  local checksums=(*.sha256) assets=()
  (( ${#checksums[@]} > 0 ))
  for checksum in "${checksums[@]}"; do
    binary=${checksum%.sha256}
    [[ "$binary" =~ ^smaug-[a-zA-Z0-9.-]+$ && -s "$binary" ]]
    IFS= read -r entry < "$checksum"
    [[ "${entry:0:64}" =~ ^[a-f0-9]{64}$ && "${entry:64}" == "  $binary" ]]
    sha256sum --strict --check -- "$checksum"
    assets+=("$binary" "$checksum")
  done
  gh release upload "$VERSION" "${assets[@]}" --clobber
)

runtime="$stage/runtime"
gh run download "$CANDIDATE_RUN" --name installer-runtime --dir "$runtime"
for edition in backend full; do
  jq -e --slurpfile record "$CANDIDATE" '
    .version == $record[0].version and .commit == $record[0].tested
    and .images.backend == $record[0].backend and .images.frontend == $record[0].frontend
    and .snapshot != null' "$runtime/$edition/manifest.json" >/dev/null
done
for bundle in "smaug-backend-$VERSION" "smaug-full-$VERSION"; do
  for extension in zip tar.gz; do
    [[ -s "$runtime/$bundle.$extension.sha256" ]]
  done
done
upload_verified "$runtime"
for system in linux windows darwin; do
  for arch in amd64 arm64; do
    native="$stage/$system-$arch"
    gh run download "$CANDIDATE_RUN" --name "native-$system-$arch" --dir "$native"
    upload_verified "$native"
    rm -rf -- "$native"
  done
done

(
  cd website
  npm ci
  SMAUG_RELEASE_TAG="$VERSION" SMAUG_RELEASE_DRAFT_ID="$draft_id" npm run prepare:release
  npm run build
  mv dist "$RUNNER_TEMP/smaug-pages-current"
)
tar -czf "$stage/smaug-pages.tar.gz" -C "$RUNNER_TEMP/smaug-pages-current" .
gh release upload "$VERSION" "$stage/smaug-pages.tar.gz" --clobber
echo 'published=false' >> "$GITHUB_OUTPUT"
