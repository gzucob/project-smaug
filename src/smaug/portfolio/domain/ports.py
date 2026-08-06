"""Portfolio domain ports — the dependency boundary for ticker resolution.

``CompanyRegistry`` is a ``Protocol`` (structural typing, no ABC) like every
other boundary in the codebase. It is a ``ports.py`` rather than a
``repositories.py`` because its only implementation is an HTTP data source (the
CVM FCA archive), not storage — the same distinction ``analysis`` draws for its
``PriceProvider`` (see ``src/smaug/AGENTS.md``).

``PortfolioRepository`` sits in the same file even though it *is* storage
(Postgres) — this context has no separate ``repositories.py`` yet, and one port
each does not earn a second file (#151).
"""

from __future__ import annotations

from collections.abc import Iterable
from typing import Protocol

from smaug.portfolio.domain.company import CompanyIdentity
from smaug.portfolio.domain.entities import PortfolioTicker
from smaug.portfolio.domain.universe import ListedCompany


class CompanyRegistry(Protocol):
    """Resolve a B3 ticker to its CVM registrant keys, and list the universe."""

    async def resolve(self, ticker: str) -> CompanyIdentity | None:
        """Return the identity for ``ticker``, or ``None`` if it is not listed."""
        ...

    async def resolve_all(self, tickers: Iterable[str]) -> dict[str, CompanyIdentity]:
        """Resolve many tickers at once; unlisted ones are absent from the dict."""
        ...

    async def companies(self) -> tuple[ListedCompany, ...]:
        """Every listed company, the unit a whole-exchange run iterates."""
        ...


class PortfolioRepository(Protocol):
    """The user's chosen set of watched tickers — stored, not compiled (#151,
    ADR 0049).

    ``add``/``remove`` are idempotent: favoriting an already-favorited ticker,
    or un-favoriting one that is not there, is a no-op rather than an error —
    the natural semantics for a toggle button, which does not want to handle a
    double-click/double-tap as a failure.
    """

    async def list(self) -> list[PortfolioTicker]:
        """Every favorited ticker, oldest favorite first."""
        ...

    async def add(self, ticker: str) -> PortfolioTicker:
        """Favorite ``ticker``; returns the persisted entry either way — a
        fresh one, or the one that was already there (``added_at`` unchanged).
        """
        ...

    async def remove(self, ticker: str) -> None: ...
