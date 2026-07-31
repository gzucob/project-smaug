"""B3's *Classificação Setorial*, read from the exchange's listed-company API.

The three-level economic taxonomy is a B3 artifact — CVM open data carries only
the single coarse ``Setor_Atividade`` (ADR 0024). B3 publishes the taxonomy as a
weekly file whose URL carries a content hash, and links it from a page rendered
by JavaScript; so the practical source is the endpoint its own listing pages
call, which answers per company with ``industryClassification`` as three levels
separated by ``" / "``.

**Keyed on ``CD_CVM``, and the company's tickers come from our registry, not
from B3's reply.** The reply does carry an ``otherCodes`` list, and using it
would be a trap: it is B3's *current* view, so Eletrobras (CD_CVM 2437) answers
with AXIA3/AXIA7 after its rebrand, while the FCA archive we ingested still says
ELET3/5/6 — and it mixes in debentures and subscription receipts (``PETR-DEB62``,
``RENT99``). Asking B3 "how is registrant X classified" and answering "so are all
the codes *we* know X trades under" avoids both.

## The seven mangled labels

The endpoint returns labels with commas replaced by periods, and some names
abbreviated — ``Petróleo. Gás e Biocombustíveis``,
``Serv.Méd.Hospit..Análises e Diagnósticos``. A blanket period→comma rule would
corrupt the legitimate abbreviations (``Máq. e Equip.`` would become
``Máq, e Equip,``), so the fix is a **lookup, hand-verified once**, and a label
carrying a period that is *not* in the table is reported as unknown rather than
guessed. The table is small on purpose: it is reference data, like
``cvm_codes.py``, not a heuristic.
"""

from __future__ import annotations

import asyncio
import base64
import json
from dataclasses import dataclass

import httpx

from smaug.portfolio.domain.taxonomy import Classification
from smaug.portfolio.domain.universe import ListedCompany
from smaug.shared.logging import get_logger

logger = get_logger(__name__)

B3_LISTED_BASE_URL = (
    "https://sistemaswebb3-listados.b3.com.br/listedCompaniesProxy/CompanyCall"
)

# The endpoint rejects a bare browser-less request; it wants a UA like its own
# pages send. Nothing else about the call is special.
_USER_AGENT = "Mozilla/5.0"

_LEVEL_SEPARATOR = " / "

# What the endpoint writes -> the label B3 itself publishes. Every entry was
# checked by hand, once, and the source of each check is recorded:
#
#   * The first five are corroborated by this repository's own previously
#     hand-verified snapshot (the ten portfolio tickers plus the five sector
#     representatives), which was read off B3's public classification tool.
#   * The last two are the abbreviation `Máq. e Equip.`, whose expansion is
#     corroborated by the *subsetor* level of that same verified snapshot
#     ("Máquinas e Equipamentos", WEGE3) and by B3's published segment list.
#
# A label with a period that is missing from here is NOT corrected — it is
# reported, so a new one gets the same single inspection rather than a guess.
LABEL_CORRECTIONS: dict[str, str] = {
    "Petróleo. Gás e Biocombustíveis": "Petróleo, Gás e Biocombustíveis",
    "Exploração. Refino e Distribuição": "Exploração, Refino e Distribuição",
    "Motores . Compressores e Outros": "Motores, Compressores e Outros",
    "Tecidos. Vestuário e Calçados": "Tecidos, Vestuário e Calçados",
    "Serv.Méd.Hospit..Análises e Diagnósticos": (
        "Serviços Médico-Hospitalares, Análises e Diagnósticos"
    ),
    "Máq. e Equip. Construção e Agrícolas": (
        "Máquinas e Equipamentos de Construção e Agrícolas"
    ),
    "Máq. e Equip. Industriais": "Máquinas e Equipamentos Industriais",
}


@dataclass(frozen=True)
class B3Fetch:
    """What one refresh read from B3: the classifications, and what it could not."""

    # ticker -> its three-level classification
    classifications: dict[str, Classification]
    # companies B3 answered nothing for — almost always in judicial recovery,
    # liquidation or bankruptcy, which B3 stops classifying
    unclassified: tuple[str, ...]
    # labels carrying a period that no correction covers: a new mangling, which
    # needs one human look before it can be trusted on a screen
    unknown_labels: tuple[str, ...]


def _corrected(label: str) -> tuple[str, bool]:
    """A label as B3 publishes it, and whether it still looks mangled."""
    fixed = LABEL_CORRECTIONS.get(label)
    if fixed is not None:
        return fixed, False
    return label, "." in label


class B3TaxonomySource:
    """Read the three-level classification for a set of companies from B3."""

    def __init__(
        self,
        http_client: httpx.AsyncClient,
        *,
        base_url: str | None = None,
        delay_seconds: float = 0.0,
    ) -> None:
        self._http = http_client
        self._base_url = (base_url or B3_LISTED_BASE_URL).rstrip("/")
        self._delay = delay_seconds

    async def fetch(self, companies: tuple[ListedCompany, ...]) -> B3Fetch:
        classifications: dict[str, Classification] = {}
        unclassified: list[str] = []
        unknown: set[str] = set()

        for company in companies:
            raw = await self._industry(company.cd_cvm)
            if raw is None:
                unclassified.append(company.ticker)
                continue
            levels = [part.strip() for part in raw.split(_LEVEL_SEPARATOR)]
            if len(levels) != 3:
                # B3 has always answered with three; a different shape is a
                # change in the source, not something to pad or truncate.
                logger.warning(
                    "B3 classification for %s has %d level(s): %r",
                    company.ticker,
                    len(levels),
                    raw,
                )
                unclassified.append(company.ticker)
                continue
            fixed: list[str] = []
            for level in levels:
                label, suspect = _corrected(level)
                if suspect:
                    unknown.add(level)
                fixed.append(label)
            classification = Classification(fixed[0], fixed[1], fixed[2])
            for ticker in company.tickers:
                classifications[ticker] = classification
            if self._delay:
                await asyncio.sleep(self._delay)

        return B3Fetch(
            classifications=classifications,
            unclassified=tuple(unclassified),
            unknown_labels=tuple(sorted(unknown)),
        )

    async def _industry(self, cd_cvm: str) -> str | None:
        """``industryClassification`` for one registrant, or ``None``."""
        payload = base64.b64encode(
            json.dumps({"codeCVM": cd_cvm, "language": "pt-br"}).encode()
        ).decode()
        url = f"{self._base_url}/GetDetail/{payload}"
        try:
            response = await self._http.get(
                url, headers={"User-Agent": _USER_AGENT}, timeout=20.0
            )
        except httpx.HTTPError as exc:
            logger.warning("B3 detail failed for %s: %s", cd_cvm, exc)
            return None
        if response.status_code != httpx.codes.OK or not response.text.strip():
            # An empty body is how the endpoint says "no such listed company",
            # which is the normal answer for a delisted or bankrupt filer.
            return None
        try:
            body = response.json()
        except ValueError:
            logger.warning("B3 detail for %s was not JSON", cd_cvm)
            return None
        industry = body.get("industryClassification")
        return industry if isinstance(industry, str) and industry.strip() else None
