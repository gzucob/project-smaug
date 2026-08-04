# 0049 — The portfolio is a favorite, not a form

- **Status:** Accepted
- **Date:** 2026-08-04

## Context

The portfolio (which tickers the user watches) was a hardcoded 9-entry dict,
duplicated by hand in the backend (`portfolio/domain/sectors.py`) and the
frontend (`frontend/src/lib/sectors.ts`). Changing it meant editing code and
redeploying (#151). The batch-ingestion half of #151 ("the universe is the
registry") already shipped in #109 — `smaug analyze --all`/`smaug doctor --all`
already sweep every FCA-listed company; what remained was making the
*portfolio itself* user data.

Three shapes were on the table for how a user adds a ticker: a dedicated
"manage portfolio" form with registry-backed validation; a CLI command; or a
favorite toggle on the ticker page the user is already looking at. The
project owner's own framing settled it: *"não vou montar uma carteira com
integração com a B3... o user procura um ticker, entra nele e tem um coração
pra adicionar favoritos/carteira."* No separate management surface, no
validation beyond what a page that already rendered real analysis data
already implies.

## Decision

**A ticker is favorited from its own page, not from a form.** The API's
`POST /portfolio/{ticker}` validates only the *shape* of a B3 trading code
(`portfolio.domain.universe.is_trading_code`, a regex already used to build
the whole-exchange universe) — never a live CVM/FCA registry lookup. The
front-end only ever shows the favorite button on a ticker page that has
already loaded real analysis data (`GET /analysis/{ticker}` succeeded), so by
the time `add` is called the ticker's validity is established by something
upstream of this endpoint, not by this endpoint re-confirming it.

**Membership, not history.** The new `portfolio` table has one row per
favorited ticker (`ticker` itself the primary key, `added_at` the only other
column) — unlike `ticker_analysis`, which is append-only and keeps every
computation. `add`/`remove` are idempotent: favoriting an already-favorited
ticker or un-favoriting an absent one is a no-op, the natural semantics for a
toggle button that must not treat a double-click as a failure.

**Two declarative bases, one Postgres database.** `portfolio` gets its own
`DeclarativeBase` (`portfolio/infrastructure/sqlalchemy_models.py`), separate
from `analysis`'s — the two contexts share a database, never a schema file,
matching how Mongo and Postgres models already never leak across contexts.
`alembic/env.py`'s `target_metadata` becomes a list of both bases' metadata.

**CORS is opened for exactly one caller, never the browser.** Every existing
read stays server-side (`RULES_FRONTEND`: "no CORS surface"). The favorite
toggle is the first mutation the front-end has ever made, and it is proxied
through a same-origin Next.js Route Handler rather than calling FastAPI
directly from the browser — so the API's new `CORSMiddleware` only ever needs
to admit that one Next.js server (`Settings.api_cors_origins`), and the
browser's own cross-origin exposure stays exactly zero.

## Consequences

**Gained**: adding or removing a favorite needs no deploy, no PR, no code
change — the actual ask of #151. The CLI's default ticker set
(`smaug analyze`/`smaug ingest`/`smaug report`/`smaug doctor` with neither
`--ticker` nor `--all`) now reads the same table the product writes to, so
the two can never drift the way the backend and frontend hardcoded copies
already had.

**Cost**: a ticker can be favorited that never resolves to anything — a
shaped-but-nonexistent code, or one CVM has simply never listed. This is
accepted, not fixed: the failure is the same one the CLI already tolerates
for an unknown `--ticker`, `analyze` skips it with "no CVM fundamentals" and
nothing more dramatic happens. Registry validation was considered and
rejected — it would add a live FCA lookup (an HTTP call plus a ZIP
download/cache) to what is otherwise a Postgres-only request path, for a
mistake the product's own navigation already makes unlikely.

**Ruled out**: a management page with its own ticker-entry field. The
project owner was explicit that this is not being built — the favorite lives
where the user already is, not in a second surface they would have to learn.

**Not reached**: `share_classes.py`/`listings.py`/`PORTFOLIO`
(`sectors.py`) — the curated per-ticker overrides for classification, share
composition and listing-floor bounds — are untouched. #151's own text called
these out as a *fallback/override layer*, not the membership list; this
decision only replaces what decided which tickers get analyzed by default.
