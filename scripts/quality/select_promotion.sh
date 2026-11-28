#!/usr/bin/env bash
# Resolve a merge on main to the same repository's successful PR validation.
set -euo pipefail

repo="repos/$GITHUB_REPOSITORY"
read -r merged base head extra <<< "$(git rev-list --parents -1 "$GITHUB_SHA")"
[[ "$merged" == "$GITHUB_SHA" && -n "$base" && -n "$head" && -z "$extra" ]]
pr=$(gh api "$repo/commits/$merged/pulls" | jq -ec \
  --arg repo "$GITHUB_REPOSITORY" --arg merged "$merged" --arg head "$head" '
    [.[] | select(.merged_at and .merge_commit_sha == $merged and .base.ref == "main"
      and .head.ref == "develop" and .head.repo.full_name == $repo and .head.sha == $head)]
    | if length == 1 then .[0] else error("Expected one merged develop-to-main PR") end')
number=$(jq -r '.number' <<< "$pr")
mode=release
reason=''
if jq -e 'any(.labels[]; .name == "no-release")' <<< "$pr" >/dev/null; then
  mode=no-release
  reason=$(jq -er '.body | split("\n") |
    map(select(test("^No-release reason: [^[:space:]].*"))) |
    if length == 1 then .[0] else error("Expected one bypass reason") end' <<< "$pr")
fi

run=$(gh api "$repo/actions/workflows/promotion.yml/runs?event=pull_request&head_sha=$head&per_page=50" |
  jq -ec --arg repo "$GITHUB_REPOSITORY" --arg head "$head" '
    [.workflow_runs[] | select(.head_repository.full_name == $repo and .head_sha == $head
      and .head_branch == "develop")] | sort_by(.run_number) | last
    | if .conclusion == "success" then . else error("Newest candidate has not passed") end')
run_id=$(jq -r '.id' <<< "$run")
attempt=$(jq -r '.run_attempt' <<< "$run")
dir=$(mktemp -d "$RUNNER_TEMP/smaug-promotion.XXXXXXXX")
gh run download "$run_id" --name "promotion-$attempt" --dir "$dir"
candidate="$dir/promotion.json"
tree=$(git rev-parse "$merged^{tree}")
jq -e --arg mode "$mode" --arg tree "$tree" --arg base "$base" --arg head "$head" \
  --arg reason "$reason" --argjson pr "$number" --argjson run "$run_id" \
  --argjson attempt "$attempt" '
    .mode == $mode and .tree == $tree and .base == $base and .head == $head
    and .reason == $reason and .pr == $pr and .run == $run and .attempt == $attempt
    and (.tested | test("^[a-f0-9]{40}$"))' "$candidate" >/dev/null

if [[ "$mode" == release ]]; then
  version=$(jq -er '.version | select(test("^[0-9]{4}\\.[0-9]{2}\\.[0-9]{2}-[a-f0-9]{12}$"))' "$candidate")
  tested=$(jq -er .tested "$candidate")
  [[ "$version" == *-"${tested:0:12}" ]]
  printf 'version=%s\n' "$version" >> "$GITHUB_OUTPUT"
fi
printf 'mode=%s\nrun=%s\nattempt=%s\ncandidate=%s\n' "$mode" "$run_id" "$attempt" "$candidate" >> "$GITHUB_OUTPUT"
