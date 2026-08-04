"""Manage the user's favorited tickers (#151, ADR 0049).

Thin on purpose: the only rule this use case enforces is that a ticker offered
to ``add`` at least has the *shape* of a B3 trading code — not that it is a
real, currently-listed one. There is no registry lookup here, deliberately: a
ticker only ever gets a favorite button once its own page has already loaded
real analysis data, so by the time ``add`` is called its validity is already
established by something upstream of this use case, not by this use case
re-confirming it against the CVM.
"""

from __future__ import annotations

from smaug.portfolio.domain.entities import PortfolioTicker
from smaug.portfolio.domain.ports import PortfolioRepository
from smaug.portfolio.domain.universe import is_trading_code
from smaug.shared.errors import UnknownTickerError


class ManagePortfolioUseCase:
    """Orchestrates the ``PortfolioRepository`` port — no I/O of its own."""

    def __init__(self, repository: PortfolioRepository) -> None:
        self._repository = repository

    async def list(self) -> list[PortfolioTicker]:
        return await self._repository.list()

    async def add(self, ticker: str) -> PortfolioTicker:
        symbol = ticker.strip().upper()
        if not is_trading_code(symbol):
            raise UnknownTickerError(symbol)
        return await self._repository.add(symbol)

    async def remove(self, ticker: str) -> None:
        await self._repository.remove(ticker.strip().upper())
