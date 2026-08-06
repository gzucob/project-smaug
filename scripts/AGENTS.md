# Script Rules

These rules apply to repository scripts. The root `AGENTS.md` remains in force.

- Scripts that import application code follow the architecture, typing and
  style rules in `src/smaug/AGENTS.md`.
- Preserve the distinction between reproducible project tooling and one-off
  operational scripts; document the required environment and side effects.
- Generated reports and fixtures must follow `docs/AGENTS.md`.
- Run the relevant quality gate in `.github/AGENTS.md` before committing.
