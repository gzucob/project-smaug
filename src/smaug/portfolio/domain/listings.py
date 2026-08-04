"""When each curated ticker was admitted to listing on B3.

**Not consulted for whether the analysis produces a row.** That question used
to be asked of this column (`#153`) and no longer is (ADR 0048): a cross-check
against B3's own tape found this date wrong at exchange scale in both
directions — WEGE3 reads 2007-06-22 though WEG has traded since the 1970s, and
Natura's own record (outside the curated nine, so read straight off the
registry) reads 2025, decades after B3 shows it trading. Neither error is safe
to build a "not yet listed" claim on, so `AnalyzePortfolioUseCase` now asks B3's
own tape instead (`_not_yet_traded`) and no longer imports this module at all.

What this date still bounds is a **code-succession chain**
(`analysis.infrastructure.succession.CodeSuccession`): when walking backwards
through a security's earlier trading codes, a candidate whose sessions stop
before this date cannot be this security's earlier self — it belonged to
whatever the registrant was before this listing existed. A floor for that walk,
not a birth certificate for pricing.

The date is the FCA's ``Data_Inicio_Listagem``. Its neighbour
``Data_Inicio_Negociacao`` is the start of trading **in the current listing
segment**, not the instrument's debut: it reads 2018-05-14 for PETR4 and
2017-12-22 for VALE3, which are their Nível 2 and Novo Mercado migrations —
using it would have bounded the chain walk too late.

Curated here for the nine for the same reason ``LISTED_CLASSES`` is: they keep
their verified keys and never trigger an FCA download (``_registry_identities``).
Any other ticker resolves from the registry on demand, which reads the same
column, uncurated.
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
