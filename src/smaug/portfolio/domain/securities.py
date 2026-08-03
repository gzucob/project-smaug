"""The trading codes one share class has been listed under, over time.

A B3 trading code is not an identity. A company renames it and nothing else
changes (EMBR3 → EMBJ3, November 2025), or it carries the code through a merger
it survived and renames both at once (ARZZ3 → AZZA3). The exchange files each
year's quotes under the code that traded *that* year, so read under today's code
alone the earlier years belong to nobody (#193).

What survives the change is the **security**: the same registrant's same share
class. That is the CVM's own key — ``CNPJ`` plus the class the FCA files the code
under — and it is the key the statements already use (ADR 0030), which is what
keeps a company's price and its filings on one entity.

**The FCA carries the trading code only from 2018**, and only as of each year's
last filing: the ``Codigo_Negociacao`` column is empty in every earlier year, and
CVM publishes one version per year, so a code retired before its registrant filed
a full cycle under it is never named at all. ``KROT3`` appears nowhere in the
archive — Cogna's union of codes is ``('COGN3',)`` — and the same holds for
``TBLE3`` (renamed 2016) and ``RUMO3`` (2017). A rename older than the column is
therefore not recoverable from here, which is why the price side treats a year it
cannot name a code for as a structural null rather than serving the fraction of
the year it happens to hold.
"""

from __future__ import annotations

from collections.abc import Callable

# Ticker -> every *other* code the same registrant has filed for that ticker's
# own share class. Unordered: which of them precedes which is a question about
# trading sessions, not about the cadastre, and it is answered where the sessions
# are (``analysis.domain.succession``). Empty for the overwhelming majority of
# codes, which have only ever been themselves.
SiblingCodesResolver = Callable[[str], tuple[str, ...]]


def no_siblings(ticker: str) -> tuple[str, ...]:
    """The default resolver: every code stands alone."""
    return ()


# The first FCA year whose securities member carries ``Codigo_Negociacao`` at
# all. Measured over the published archive: 2010-2017 file the securities with
# the column blank (658 rows, 0 codes in 2015), 2018 is the first with it filled
# (751 rows, 477 codes).
FIRST_YEAR_WITH_TRADING_CODES = 2018


def share_class_suffix(code: str) -> str | None:
    """The class digit of a B3 equity code, or ``None`` if it is not one.

    Matched on the exact digit rather than on ON/PN, because 5 and 6 are PNA and
    PNB — different classes that trade at different prices (#72). A unit (``11``)
    is deliberately not a class here: it bundles two of them and sits on its own
    share base, so it can never inherit an ON's series nor lend it one.
    """
    if len(code) != 5:
        return None
    suffix = code[4]
    return suffix if suffix in "3456" else None
