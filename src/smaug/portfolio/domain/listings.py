"""When each curated ticker was admitted to listing on B3.

A closed year that ends before a ticker was listed cannot have a price in any
source — that is a fact about the world, not a gap of ours, and it is what lets
the analysis call such a null deliberate rather than a warning (#153).

The date is the FCA's ``Data_Inicio_Listagem``, and the column matters. Its
neighbour ``Data_Inicio_Negociacao`` is the start of trading **in the current
listing segment**, not the instrument's debut: it reads 2018-05-14 for PETR4 and
2017-12-22 for VALE3, which are their Nível 2 and Novo Mercado migrations. Using
it would have declared Vale unlisted in 2016.

Curated here for the nine for the same reason ``LISTED_CLASSES`` is: they keep
their verified keys and never trigger an FCA download (``_registry_identities``).
Any other ticker resolves from the registry on demand, which reads the same
column.

**The column is a floor, not a birth certificate.** It records admission to
listing as the FCA now states it, and a company that migrated segments can carry
the later date — WEGE3 reads 2007-06-22 though WEG has traded since the 1970s.
That is why the analysis only calls a year "not yet listed" when it *also* found
no price anywhere: the date alone may be late, and being late is precisely the
error this exists to avoid making in the other direction.
"""

from __future__ import annotations

from datetime import date

# Read from ``fca_cia_aberta_valor_mobiliario_2024.csv``, highest-version
# still-trading row per ticker. Sanity-checked against each company's known
# debut: BBDC4 1946 (Bradesco), VALE3 1968, BBSE3 2013 and CXSE3 2021 (their
# IPOs), SAPR11 2017 (when the unit itself was created, its ON/PN being older).
LISTED_SINCE: dict[str, date] = {
    "BBAS3": date(1977, 7, 20),
    "BBDC4": date(1946, 11, 26),
    "BBSE3": date(2013, 4, 29),
    "CXSE3": date(2021, 4, 29),
    "PETR4": date(1977, 7, 20),
    "SAPR11": date(2017, 11, 22),
    "TAEE11": date(2006, 10, 26),
    "VALE3": date(1968, 4, 1),
    "WEGE3": date(2007, 6, 22),
}


def listed_since(ticker: str) -> date | None:
    """When ``ticker`` was admitted to listing, or ``None`` if not curated."""
    return LISTED_SINCE.get(ticker.upper())
