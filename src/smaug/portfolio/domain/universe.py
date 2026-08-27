"""The listed universe — which companies a whole-exchange run iterates.

Resolving one ticker on demand still needs to ask what the FCA says it *is*.
A batch walks the FCA index end to end, where ``Codigo_Negociacao`` contains
both malformed non-codes and valid-looking codes for warrants and terminated
securities. The current fundamental universe therefore requires both a B3 code
shape and an FCA identity that is a still-trading share or unit (ADR 0053).

**The unit of the universe is the company, not the ticker.** A registrant files
one set of statements; its ON and PN classes are two prices over that one filing
(ADR 0014). ELET3/ELET5/ELET6 are one company, and iterating tickers would mirror
its DFP three times. So the batch iterates ``ListedCompany`` — 368 of them in
2024, against 506 trading codes — and the classes come back into play only where
they differ, which is price and therefore analysis.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass

from smaug.portfolio.domain.company import (
    CompanyIdentity,
    fundamental_exclusion,
)
from smaug.portfolio.domain.company import (
    is_trading_code as _is_trading_code,
)

# Which class a company is named by when it lists several. ON first: it is the
# share that carries the company's name in every reference, and the one a reader
# means by "the company". The rest fall back to the code's own order, so the
# choice never depends on how the CSV happened to be sorted.
_PRIMARY_ORDER: tuple[str, ...] = ("3", "4", "11", "5", "6")


def is_trading_code(ticker: str) -> bool:
    """Whether ``ticker`` has the syntax of a B3 security code."""
    return _is_trading_code(ticker)


@dataclass(frozen=True, slots=True)
class ListedCompany:
    """One CVM registrant and every B3 trading code it lists."""

    cd_cvm: str
    cnpj: str
    denom: str
    ticker: str  # the primary code — ON when the company lists one
    tickers: tuple[str, ...]


def _primary(tickers: tuple[str, ...]) -> str:
    """The code a company is named by: ON if listed, else the lowest class."""

    def rank(ticker: str) -> tuple[int, str]:
        suffix = ticker[4:]
        order = (
            _PRIMARY_ORDER.index(suffix)
            if suffix in _PRIMARY_ORDER
            else len(_PRIMARY_ORDER)
        )
        return order, ticker

    return min(tickers, key=rank)


def listed_companies(
    identities: Iterable[CompanyIdentity],
) -> tuple[ListedCompany, ...]:
    """Group current equity identities into the companies a batch run iterates.

    Ordered by ``cd_cvm`` so a run is reproducible: a partial batch resumed later
    covers the same ground in the same order, whatever order the index was built
    in.
    """
    grouped: dict[str, list[CompanyIdentity]] = {}
    for identity in identities:
        if (
            not is_trading_code(identity.ticker)
            or fundamental_exclusion(identity) is not None
        ):
            continue
        grouped.setdefault(identity.cd_cvm, []).append(identity)

    companies: list[ListedCompany] = []
    for cd_cvm, members in grouped.items():
        tickers = tuple(sorted(m.ticker.upper() for m in members))
        first = members[0]
        companies.append(
            ListedCompany(
                cd_cvm=cd_cvm,
                cnpj=first.cnpj,
                denom=first.denom,
                ticker=_primary(tickers),
                tickers=tickers,
            )
        )
    return tuple(sorted(companies, key=lambda c: c.cd_cvm))
