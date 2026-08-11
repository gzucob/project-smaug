"""How a reader addresses the raw primary-source mirror.

One line, in one place, because getting it wrong is silent: a filter that names
the wrong key returns an empty cursor, and an empty cursor reads as a company
that filed nothing rather than as a query that asked the wrong question.

The mirror is keyed on both the public source and the registrant (ADR 0030) —
ELET3, ELET5 and ELET6 share one filing, and one copy of it. A ticker whose
registrant cannot be named falls back to the ticker key, which is what every CVM
document had before the key moved. B3 modules keep the same registrant key but
must name ``source="b3"``; source provenance is part of content identity.
"""

from __future__ import annotations

from typing import Any

from smaug.portfolio.domain.company import RegistrantResolver


def mirror_filter(
    ticker: str,
    registrant: RegistrantResolver,
    *,
    source: str = "cvm",
    **extra: Any,
) -> dict[str, Any]:
    """Select one source's documents for ``ticker``, keyed by registrant."""
    code = registrant(ticker)
    key: dict[str, Any] = {"cvm_code": code} if code is not None else {"ticker": ticker}
    return {"source": source, **key, **extra}


def no_registrant(_ticker: str) -> None:
    """The default resolver: name no registrant, so reads fall back to the ticker."""
    return None
