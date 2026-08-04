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
therefore not recoverable from the code column at all — it is recovered from the
tape, proposed by the seam and confirmed by the **names** below, which reach back
to 2010 (ADR 0044).
"""

from __future__ import annotations

import re
import unicodedata
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

# The first year of the FCA dataset at all. Its *names* are read from here,
# and they are the point: a company that renamed is only ever named by the
# years before it did, so the record of what a registrant used to be called
# lives entirely in the years whose code column is blank (#198).
FIRST_FCA_YEAR = 2010


# Ticker -> every name CVM has filed for its registrant, folded by ``name_key``.
# What it is for is confirming a code the cadastre cannot name: B3 prints its own
# abbreviation of the company beside each code, and a registrant that once filed
# that name is the same company (#198).
RegistrantNamesResolver = Callable[[str], frozenset[str]]


def no_names(ticker: str) -> frozenset[str]:
    """The default resolver: nothing was ever filed under another name."""
    return frozenset()


# The furniture every Brazilian corporate name carries, which says nothing about
# *which* company it is. Removed before comparing so that "CIA" and "S.A." cannot
# be what two names have in common.
_FURNITURE = re.compile(
    r"\b(S\.?A\.?|S/A|CIA|COMPANHIA|PARTICIPACOES|PARTICIPACAO|HOLDING|DO|DA|DE|"
    r"DOS|DAS|E|EM|RECUPERACAO|JUDICIAL|EXTRAJUDICIAL)\b"
)


def name_key(name: str) -> str:
    """A company name reduced to what two sources can be expected to share.

    Accents dropped, punctuation dropped, corporate furniture dropped. B3 writes
    the same company as ``AMBEV S/A`` and CVM as ``AMBEV S.A.`` (#191), and B3's
    field is twelve characters wide, so nothing finer than this survives both.
    """
    decomposed = unicodedata.normalize("NFKD", name.upper())
    plain = "".join(c for c in decomposed if not unicodedata.combining(c))
    plain = re.sub(r"\s+", " ", re.sub(r"[^A-Z0-9 ]", " ", plain)).strip()
    return re.sub(r"\s+", " ", _FURNITURE.sub(" ", plain)).strip()


def confirms_name(filed: frozenset[str], printed: str) -> bool:
    """Whether a registrant ever filed under the name B3 printed on the tape.

    Compared on the first word only, because that is all the two sources agree
    on: B3 truncates to twelve characters *and* abbreviates inside them —
    ``ALL AMER LAT`` for "ALL América Latina Logística", ``RUMO LOG`` for a
    company CVM files as "Rumo S.A.". Either may be the shorter, so the test runs
    both ways.

    Loose on its own, and never used on its own: over the years CVM *does* name
    the trading code — a labelled set of 1,418 answers — this rule alone is wrong
    8.1% of the time, reading "Banco do Estado do Pará" as Banco Pan. It exists
    to reject a coincidence the tape proposed, never to propose one (#198).
    """
    tape = name_key(printed).split()
    if not tape:
        return False
    head = tape[0]
    return any(
        words and (words[0].startswith(head) or head.startswith(words[0]))
        for words in (name.split() for name in filed)
    )


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
