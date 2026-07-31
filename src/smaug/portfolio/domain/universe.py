"""The listed universe — which companies a whole-exchange run iterates.

Resolving one ticker on demand never had to ask what a ticker *is*: nobody types
``N/A``. A batch does, because it walks the FCA index end to end, and that index
carries whatever the filer typed into ``Codigo_Negociacao`` — 41 of the 547 rows
in the 2024 archive are not trading codes at all, but registration numbers
(``1545-8``, ``022055``), fragments (``N/A``, ``NAO HA``, ``ADR``) or a bare
digit. So the shape of a B3 trading code becomes a rule here, and the rows that
fail it are dropped rather than ingested as companies that do not exist.

**The unit of the universe is the company, not the ticker.** A registrant files
one set of statements; its ON and PN classes are two prices over that one filing
(ADR 0014). ELET3/ELET5/ELET6 are one company, and iterating tickers would mirror
its DFP three times. So the batch iterates ``ListedCompany`` — 368 of them in
2024, against 506 trading codes — and the classes come back into play only where
they differ, which is price and therefore analysis.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import dataclass

from smaug.portfolio.domain.company import CompanyIdentity

# A B3 trading code: a four-character root plus the one- or two-digit class
# number. The root is alphanumeric because a real one is (``B3SA3`` — the
# exchange itself); an all-digit code is not a root but a registration number
# the filer put in the wrong column.
_TRADING_CODE = re.compile(r"^[A-Z0-9]{4}[0-9]{1,2}$")

# Which class a company is named by when it lists several. ON first: it is the
# share that carries the company's name in every reference, and the one a reader
# means by "the company". The rest fall back to the code's own order, so the
# choice never depends on how the CSV happened to be sorted.
_PRIMARY_ORDER: tuple[str, ...] = ("3", "4", "11", "5", "6")


def is_trading_code(ticker: str) -> bool:
    """Whether ``ticker`` has the shape of a B3 trading code.

    A shape test, not an existence test — it rejects what the FCA's free-text
    column collects, and says nothing about whether B3 lists the result.
    """
    code = ticker.strip().upper()
    return bool(_TRADING_CODE.match(code)) and not code.isdigit()


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
    """Group resolved identities into the companies a batch run iterates.

    Ordered by ``cd_cvm`` so a run is reproducible: a partial batch resumed later
    covers the same ground in the same order, whatever order the index was built
    in.
    """
    grouped: dict[str, list[CompanyIdentity]] = {}
    for identity in identities:
        if not is_trading_code(identity.ticker):
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
