"""Portfolio domain ports — the dependency boundary for ticker resolution.

``CompanyRegistry`` is a ``Protocol`` (structural typing, no ABC) like every
other boundary in the codebase. It is a ``ports.py`` rather than a
``repositories.py`` because its only implementation is an HTTP data source (the
CVM FCA archive), not storage — the same distinction ``analysis`` draws for its
``PriceProvider`` (see ``.claude/RULES/RULES_LAYERS.md``).
"""

from __future__ import annotations

from collections.abc import Iterable
from typing import Protocol

from smaug.portfolio.domain.company import CompanyIdentity


class CompanyRegistry(Protocol):
    """Resolve a B3 ticker to its CVM registrant keys."""

    async def resolve(self, ticker: str) -> CompanyIdentity | None:
        """Return the identity for ``ticker``, or ``None`` if it is not listed."""
        ...

    async def resolve_all(self, tickers: Iterable[str]) -> dict[str, CompanyIdentity]:
        """Resolve many tickers at once; unlisted ones are absent from the dict."""
        ...
