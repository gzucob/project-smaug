# Git and GitHub Workflow Rules

These rules apply to branches, commits, GitHub issues, workflows and pull
requests. The root `AGENTS.md` remains in force.

## Branches

- The default branch is `main`. It is protected and accepts changes only
  through pull requests and squash merges; never push directly to it.
- Working branch prefixes are `feat/`, `fix/`, `refactor/`, `test/`, `chore/`
  and `docs/`.
- Use a short technical kebab-case scope, preferably under 40 characters.
- Keep one logical unit of work per branch. Split unrelated concerns.
- Create a branch from an up-to-date `main`:

```bash
git checkout main
git pull
git checkout -b <prefix>/<scope>
```

- Fix CI failures on the same branch and pull request.
- After a squash merge, update local `main` and delete the local branch.

## Quality Gate

Before every commit that can affect Python behavior, run:

```bash
uv run ruff check .
uv run ruff format --check .
uv run mypy src
uv run pytest
```

Everything must pass. If a sandbox cannot write to the default uv cache, set a
temporary command cache such as `UV_CACHE_DIR=/tmp/project-smaug-uv-cache`.

For front-end changes, also follow the gate in `frontend/AGENTS.md`.

## Commits, Push and Pull Requests

- Write commit messages in English, imperative mood and conventional style:
  `feat:`, `fix:`, `chore:`, `docs:`, `refactor:` or `test:`.
- Pull request titles must use Conventional Commits in English, optionally
  with a scope, such as `feat(analysis): ...` or `fix(ingestion): ...`.
  The `[NAMESPACE-NN]` prefix is reserved for issue titles and must not be
  used in pull request titles. Append `(#NN)` when a title closes one issue.
- One commit is one logical unit of work.
- On the branch's first push, open the pull request, as a draft if work is
  still in progress:

```bash
git push -u origin <branch>
gh pr create --base main --head <branch> --title "..." --body "..."
```

- Push after every commit so CI runs on the current state.
- Merge with squash and delete the remote branch:

```bash
gh pr merge --squash --delete-branch
```

## Issues

An issue without a namespace prefix or the required labels is incomplete.

### Title

Use `[NAMESPACE-NN] short imperative title`, in English, with at most 72
characters.

| Namespace | Area |
|---|---|
| `ING` | Ingestion: CVM/B3 readers, collection and mirror persistence |
| `ANL` | Analysis: indicators, PostgreSQL persistence and read API |
| `WEB` | Next.js front-end under `frontend/` |
| `PORT` | Portfolio ticker and sector mapping |
| `CORE` | Shared configuration, Mongo connection, EventBus and errors |
| `INFRA` | Docker, dependencies and repository configuration |
| `DX` | Tooling and local developer experience |
| `TEST` | Tests, coverage and CI |
| `DOCS` | Documentation |
| `SEC` | Security, secrets, tokens and exposure |

### Required labels

Every issue needs all three label dimensions:

- Area: `area: ingestion`, `area: analysis`, `area: frontend`,
  `area: portfolio`, `area: core`, `area: infra`, `area: docs` or
  `area: testing`. Multiple areas are allowed.
- Priority: exactly one of `priority: high`, `priority: medium` or
  `priority: low`.
- Type: exactly one of `type: feature`, `type: bug`, `type: tech-debt`,
  `type: security`, `type: docs` or `type: chore`.

### Body and closing

```markdown
## Context
## Improvement / Fix
## Implementation Notes (optional)
```

Every issue closes against a verifiable acceptance criterion. Use `Closes #NN`
in the pull request body so GitHub closes it on merge.

## Language

Code, comments, workflows, issues, commits and pull requests are in English.
PT-BR is reserved for documentation prose and user-facing text.
