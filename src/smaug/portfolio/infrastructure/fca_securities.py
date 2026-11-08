"""Resolve historical identities and classes from admitted FCA rows."""

from __future__ import annotations

import asyncio
import unicodedata
from collections.abc import Callable, Sequence
from dataclasses import dataclass

from smaug.portfolio.domain.securities import (
    PeriodShareClassesResolver,
    RegistrantNamesResolver,
    SiblingCodesResolver,
    name_key,
    share_class_suffix,
)
from smaug.portfolio.domain.share_classes import PerShareClass, TickerCodeEvidence
from smaug.shared.logging import get_logger

logger = get_logger(__name__)

_ENCODING = "latin-1"
_DELIMITER = ";"


class FcaSecurityHistory:
    """The codes each equity class has been filed under, gathered over FCA years."""

    def __init__(self) -> None:
        self._index: _Index | None = None
        self._lock = asyncio.Lock()

    async def _ensure_loaded(self) -> _Index:
        raise NotImplementedError

    async def resolver(self) -> SiblingCodesResolver:
        """Load the archive once and hand the composition root a plain callable."""
        index = await self._ensure_loaded()

        def siblings(ticker: str) -> tuple[str, ...]:
            return index.siblings.get(ticker.strip().upper(), ())

        return siblings

    async def names(self) -> RegistrantNamesResolver:
        """Every name a code's registrant has filed, folded for comparison.

        The FCA is a snapshot as of each year's own filing, so a company that
        renamed is only ever named by the *earlier* years — which is exactly what
        makes this useful: it is the record of what a registrant used to be
        called, and the tape is the record of what traded under that name (#198).
        """
        index = await self._ensure_loaded()

        def names_of(ticker: str) -> frozenset[str]:
            cnpj = index.registrant.get(ticker.strip().upper())
            return index.names.get(cnpj, frozenset()) if cnpj else frozenset()

        return names_of

    async def historical_codes(
        self,
    ) -> Callable[[str], tuple[TickerCodeEvidence, ...]]:
        """Return every FCA code witness for the ticker's economic class."""
        index = await self._ensure_loaded()

        def codes_of(ticker: str) -> tuple[TickerCodeEvidence, ...]:
            return index.codes_by_ticker.get(ticker.strip().upper(), ())

        return codes_of

    async def period_share_classes(self) -> PeriodShareClassesResolver:
        """Return the plain equity classes each registrant filed in one FCA year."""
        index = await self._ensure_loaded()

        def classes_of(cnpj: str, year: int) -> tuple[PerShareClass, ...]:
            return index.classes_by_period.get((cnpj, year), ())

        return classes_of


@dataclass(frozen=True, slots=True)
class _Index:
    """What one pass over the archive yields, keyed for the two questions asked."""

    siblings: dict[str, tuple[str, ...]]
    registrant: dict[str, str]  # code -> CNPJ
    names: dict[str, frozenset[str]]  # CNPJ -> every name it has filed
    codes_by_ticker: dict[str, tuple[TickerCodeEvidence, ...]]
    classes_by_period: dict[tuple[str, int], tuple[PerShareClass, ...]]


def _build_index(
    archives: Sequence[tuple[int, dict[str, list[dict[str, str]]]]],
) -> _Index:
    """Group every filed equity code by (registrant, class), then invert."""
    grouped: dict[tuple[str, str], set[str]] = {}
    observed: dict[tuple[str, str], dict[str, set[int]]] = {}
    registrant: dict[str, str] = {}
    names: dict[str, set[str]] = {}
    classes_by_period: dict[tuple[str, int], set[PerShareClass]] = {}
    for year, path in archives:
        for cnpj, code in _codes(year, path):
            suffix = share_class_suffix(code)
            if suffix is None:
                continue
            classes_by_period.setdefault((cnpj, year), set()).add(
                {
                    "3": PerShareClass.ORDINARY,
                    "4": PerShareClass.PREFERRED,
                    "5": PerShareClass.PREFERRED_A,
                    "6": PerShareClass.PREFERRED_B,
                }[suffix]
            )
            grouped.setdefault((cnpj, suffix), set()).add(code)
            observed.setdefault((cnpj, suffix), {}).setdefault(code, set()).add(year)
            registrant.setdefault(code, cnpj)
        for cnpj, name in _names(year, path):
            key = name_key(name)
            if key:
                names.setdefault(cnpj, set()).add(key)
    siblings: dict[str, tuple[str, ...]] = {}
    codes_by_ticker: dict[str, tuple[TickerCodeEvidence, ...]] = {}
    for group, codes in grouped.items():
        evidence = tuple(
            TickerCodeEvidence(
                symbol=code,
                filed_years=tuple(sorted(observed[group].get(code, ()))),
            )
            for code in sorted(codes)
        )
        for code in codes:
            codes_by_ticker[code] = evidence
        if len(codes) < 2:
            continue
        for code in codes:
            siblings[code] = tuple(sorted(codes - {code}))
    return _Index(
        siblings=siblings,
        registrant=registrant,
        names={cnpj: frozenset(filed) for cnpj, filed in names.items()},
        codes_by_ticker=codes_by_ticker,
        classes_by_period={
            key: tuple(sorted(classes, key=lambda item: item.value))
            for key, classes in classes_by_period.items()
        },
    )


def _names(year: int, path: dict[str, list[dict[str, str]]]) -> list[tuple[str, str]]:
    """``(CNPJ, Nome_Empresarial)`` from the year's general cadastre."""
    return [
        (cnpj, name)
        for row in _member(year, path, "geral")
        if (cnpj := (row.get("CNPJ_Companhia") or "").strip())
        and (name := (row.get("Nome_Empresarial") or "").strip())
    ]


def _member(
    year: int, path: dict[str, list[dict[str, str]]], member: str
) -> list[dict[str, str]]:
    return path.get(f"fca_cia_aberta_{member}_{year}.csv", [])


def _codes(year: int, path: dict[str, list[dict[str, str]]]) -> list[tuple[str, str]]:
    """``(CNPJ, code)`` for every equity security the year's FCA names."""
    rows: list[tuple[str, str]] = []
    for row in _member(year, path, "valor_mobiliario"):
        code = (row.get("Codigo_Negociacao") or "").strip().upper()
        cnpj = (row.get("CNPJ_Companhia") or "").strip()
        # Only shares: a debenture or a BDR line carries a code too, and a unit
        # is a different share base (see ``share_class_suffix``).
        if code and cnpj and _is_share(row.get("Valor_Mobiliario") or ""):
            rows.append((cnpj, code))
    return rows


def _is_share(valor_mobiliario: str) -> bool:
    """Whether an FCA ``Valor_Mobiliario`` label names plain ON/PN equity."""
    decomposed = unicodedata.normalize("NFKD", valor_mobiliario)
    folded = "".join(c for c in decomposed if not unicodedata.combining(c))
    return folded.strip().lower().startswith("acoes")
