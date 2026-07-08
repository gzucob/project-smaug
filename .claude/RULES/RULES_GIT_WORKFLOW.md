# Commit, Push, and PR Rules

## Quality gate (before every commit)
```bash
uv run ruff check .
uv run ruff format --check .
uv run mypy src
uv run pytest
```
Everything green before committing. If `ruff` is locally blocked by Windows
Smart App Control, GitHub CI covers it — but resolve the block when you can.

## Commit
- Messages in English, imperative mood, conventional style
  (`feat:`, `fix:`, `chore:`, `docs:`, `refactor:`, `test:`).
- One commit = one logical unit of work.

## Push and PR
- `main` is protected: no direct push. Every change goes through branch + PR.
- On the branch's first push, open the PR (draft if still in progress):
  ```bash
  git push -u origin <branch>
  gh pr create --base main --head <branch> --title "..." --body "..."
  ```
- Push after every commit — CI runs per PR/push.
- If CI fails: fix it on the same branch.
- Merge: squash (`gh pr merge --squash --delete-branch`).

## Language
Code, comments, commits, and PRs in English. Portuguese only in documentation
and user-facing text.
