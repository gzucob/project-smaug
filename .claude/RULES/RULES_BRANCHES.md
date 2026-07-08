# Branch Rules

## Default
- Default branch: `main` — protected: merge only via PR + squash, no direct push.
- Working branches use a prefix:
  - `feat/<scope>`, `fix/<scope>`, `refactor/<scope>`, `test/<scope>`,
    `chore/<scope>`, `docs/<scope>`
- Scope in kebab-case, short and technical. Prefer under 40 characters.

## Usage
- One branch per logical unit of work.
- Always create the branch from an up-to-date `main`:
  ```bash
  git checkout main
  git pull
  git checkout -b <prefix>/<scope>
  ```
- If the task mixes unrelated concerns, split it before implementing.

## Merge
- Every merge to `main` is a squash-merge (GitHub repository policy).
- Iterative commits on the branch are squashed away — only the PR's final
  commit lands on `main`.
- After the merge: the remote branch is auto-deleted; delete the local one
  too: `git checkout main && git pull && git branch -d <branch>`.
- If CI fails on the branch: fix it on the same branch — don't open another
  branch/PR.

## Language
Commit messages and PR titles/bodies in English. PT-BR only in documentation
and user-facing text.
