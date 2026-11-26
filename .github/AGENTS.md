# Git and GitHub Workflow Rules

These rules apply to branches, commits, GitHub issues, workflows and pull
requests. The root `AGENTS.md` remains in force.

## Branches

- The default development branch is `develop`; `main` contains released code.
  Both branches are protected and accept changes only through pull requests.
  Never push directly to either branch.
- Working branch prefixes are `feat/`, `fix/`, `refactor/`, `test/`, `chore/`
  and `docs/`.
- Use a short technical kebab-case scope, preferably under 40 characters.
- Keep one logical unit of work per branch. Split unrelated concerns.
- Create a work branch from an up-to-date `develop`:

```bash
git checkout develop
git pull
git checkout -b <prefix>/<scope>
```

- Fix CI failures on the same branch and pull request.
- Squash each work PR into `develop`. After merge, update local `develop` and
  delete only the merged work branch.
- Promote a release with a PR from `develop` to `main`, using a merge commit
  to preserve ancestry. Never squash release PRs or delete either long-lived
  branch. Synchronize `develop` with the released `main` before starting the
  next development cycle; do not rewrite shared history.
- PRs into `develop` run workflow validation and affected regressions. Protect
  `develop` with the `Regressions` check.
- Protect `main` with the `Main promotion` check and require the PR branch to be
  up to date. Both promotion paths use a merge commit from `develop`.
- A release PR runs all regressions and builds and verifies the final packages
  against the certified snapshot. The successful merge publishes those same
  artifacts and deploys the download site.
- For maintenance that should enter `main` without publication, add the
  `no-release` label and exactly one `No-release reason: <reason>` line to the PR
  body. Affected regressions and workflow validation still run. Wait for the
  `Main promotion` check associated with that request before merging.
- Configure `main-no-release` with `gzucob` as its only required reviewer.
  Permit the owner to approve runs they initiated. Disable administrator
  bypass for the environment and keep the approval bound to each run.
- Before switching off the old workflow, verify that the new checks are
  available and required. The first promotion uses the manual no-release path
  and must leave the public release and Pages deployment in place.

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

The local pre-commit gate requires Docker, Go, Node.js 24 with npm, and uv.
Install dependencies with `npm ci` in `frontend`, `website`, and
`scripts/quality`. Run `uv run --frozen python scripts/quality/pre_commit.py`
with the source and test changes staged. Install the repository hook with
`git config --local core.hooksPath .githooks` once the gate is green.
All required tests must pass. Each measured source file with executable lines
must reach 90% before rounding, including files that the suites never load.
The Python suite starts isolated MongoDB and PostgreSQL services and cleans
them up after the run. Go measures files compiled on the host's platform.
Launcher regressions exercise the active platform's shell.

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
gh pr create --base develop --head <branch> --title "..." --body "..."
```

- Push after every commit so CI runs on the current state.
- Merge work PRs with squash and delete the remote work branch:

```bash
gh pr merge --squash --delete-branch
```

- Release PRs use `gh pr merge --merge` without `--delete-branch`.
- Each phase in an approved implementation plan is one logical commit.
  Keep the stages in the same work PR when the approved plan calls for it.

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
