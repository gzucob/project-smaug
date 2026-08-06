# Database Migration Rules

These rules apply to Alembic migrations. The root `AGENTS.md` remains in force.

- PostgreSQL is the derived store owned by the analysis context. Migrations do
  not introduce business calculations or move persistence concerns into the
  domain or application layers.
- Keep SQLAlchemy persistence models in
  `src/smaug/analysis/infrastructure/sqlalchemy_models.py` aligned with schema
  changes.
- Treat committed migration history as immutable. Add a new revision to change
  the schema; do not rewrite an existing applied revision.
- Preserve explicit upgrade and downgrade behavior unless a migration records
  why downgrade is impossible.
- Do not introduce multi-tenant or `company_id` scoping; this is a
  single-portfolio application.
- Read `src/smaug/AGENTS.md` before changing migrations that affect entity,
  repository or infrastructure boundaries.
- Run the Python quality gate in `.github/AGENTS.md` before committing.
