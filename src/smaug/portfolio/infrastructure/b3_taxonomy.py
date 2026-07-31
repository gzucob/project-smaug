"""B3's *Classificação Setorial*, from the exchange's own spreadsheet.

The three-level economic taxonomy is a B3 artifact — CVM open data carries only
the single coarse ``Setor_Atividade`` (ADR 0024). B3 exposes it three ways, and
the difference between them is the whole design here.

**The spreadsheet is the label authority.** ``GetDownloadIndustryClassification``
returns the classification as an ``.xlsx`` with the three levels in their own
columns, spelled the way B3 publishes them — one request, no string splitting,
no mangling. It is keyed by the four-letter **trading root** (``PETR``, ``BRAV``).

**The per-company endpoint is the coverage fallback.** That root is B3's
*current* name for the company, and our universe comes from a CVM archive of a
given year — so a company that has since renamed cannot be found on the
spreadsheet at all. Eletrobras is there as ``AXIA``, and 30 of our 506 tickers
(ELET3/5/6, BRFS3, CCRO3, BRML3…) are only reachable by asking
``GetDetail`` for a ``CD_CVM``, which never changes. Measured: 414 tickers from
the spreadsheet, 444 with the fallback.

Primary source plus fallback, the same shape ADR 0013 gives the price providers,
and for the same reason: neither source is wrong, they are incomplete in
different directions.

## What the fallback has to be corrected for

``GetDetail`` serializes the three levels into one string and writes **commas as
periods** — ``Petróleo. Gás e Biocombustíveis``. What it does *not* do is
abbreviate: ``Máq. e Equip. Industriais`` and
``Serv.Méd.Hospit.,Análises e Diagnósticos`` are B3's own labels, spelled that
way in the official spreadsheet too. So the corrections cover the comma defect
and nothing else, and every one of them is verified against the spreadsheet.

A fallback label the spreadsheet's vocabulary does not contain is **reported,
not corrected** — the spreadsheet is the authority on what a label may be, so
anything outside it is a change in the source that wants one human look.

(Two other B3 surfaces exist and neither is usable: the legacy
``bvmf.bmfbovespa.com.br/InstDados/.../ClassifSetorial.zip`` still downloads but
is frozen at September 2022, and the current file on ``www.b3.com.br`` sits
behind a JavaScript-rendered page at a content-hashed URL. Projects that use the
legacy one are serving a four-year-old taxonomy.)
"""

from __future__ import annotations

import asyncio
import base64
import json
import re
import tempfile
from dataclasses import dataclass
from pathlib import Path

import httpx

from smaug.portfolio.domain.taxonomy import Classification
from smaug.portfolio.domain.universe import ListedCompany
from smaug.portfolio.infrastructure.xlsx import read_rows
from smaug.shared.logging import get_logger

logger = get_logger(__name__)

B3_LISTED_BASE_URL = (
    "https://sistemaswebb3-listados.b3.com.br/listedCompaniesProxy/CompanyCall"
)

# The endpoints refuse a request that does not look like it came from a browser.
_USER_AGENT = "Mozilla/5.0"

_LEVEL_SEPARATOR = " / "

# A B3 trading root: four characters, the ticker minus its class number.
_ROOT = re.compile(r"^[A-Z0-9]{4}$")

# Spreadsheet columns, 0-based: SETOR, SUBSETOR, SEGMENTO, then the issuer's
# trading name and its root. The three level columns are merged down, so a row
# repeats them as blanks and they are carried forward.
_COL_SETOR, _COL_SUBSETOR, _COL_SEGMENTO, _COL_ROOT = 1, 2, 3, 5

# What ``GetDetail`` writes -> what B3 publishes, for the fallback path only.
# Every entry is verified against the official spreadsheet, and they are two
# different defects:
#
#   * a comma the endpoint serialized as a period — including the space B3 keeps
#     in "Motores , Compressores", and the abbreviations it uses in
#     "Serv.Méd.Hospit.,Análises", which are B3's own and not a mangling. An
#     abbreviated label with no comma ("Máq. e Equip. Industriais") needs no
#     entry: it is already what B3 publishes.
#   * a label B3 has since **renamed**, which the per-company endpoint still
#     serves under the old name. Only one so far: the subsetor "Comércio" is
#     "Comércio Varejista" for every company on the spreadsheet — Renner,
#     Americanas, WLM — and "Comércio" appears nowhere in B3's current
#     vocabulary.
LABEL_CORRECTIONS: dict[str, str] = {
    "Petróleo. Gás e Biocombustíveis": "Petróleo, Gás e Biocombustíveis",
    "Exploração. Refino e Distribuição": "Exploração, Refino e Distribuição",
    "Tecidos. Vestuário e Calçados": "Tecidos, Vestuário e Calçados",
    "Motores . Compressores e Outros": "Motores , Compressores e Outros",
    "Serv.Méd.Hospit..Análises e Diagnósticos": (
        "Serv.Méd.Hospit.,Análises e Diagnósticos"
    ),
    "Comércio": "Comércio Varejista",
}


@dataclass(frozen=True)
class B3Fetch:
    """What one refresh read from B3: the classifications, and what it could not."""

    # ticker -> its three-level classification
    classifications: dict[str, Classification]
    # how many tickers each source answered for
    from_sheet: int
    from_detail: int
    # companies neither source classified — almost always in judicial recovery,
    # liquidation or bankruptcy, which B3 drops from the taxonomy
    unclassified: tuple[str, ...]
    # fallback labels the spreadsheet's vocabulary does not contain: a change in
    # the source, which wants a human look before it reaches a screen
    unknown_labels: tuple[str, ...]


class B3TaxonomySource:
    """Read the three-level classification: spreadsheet first, detail as fallback."""

    def __init__(
        self,
        http_client: httpx.AsyncClient,
        *,
        base_url: str | None = None,
        cache_dir: str | None = None,
    ) -> None:
        self._http = http_client
        self._base_url = (base_url or B3_LISTED_BASE_URL).rstrip("/")
        self._cache_dir = Path(cache_dir) if cache_dir else None

    async def fetch(self, companies: tuple[ListedCompany, ...]) -> B3Fetch:
        by_root, vocabulary = await self._sheet()
        logger.info("B3 spreadsheet: %d trading roots", len(by_root))

        classifications: dict[str, Classification] = {}
        from_sheet = 0
        unresolved: list[ListedCompany] = []
        for company in companies:
            matched = {t: by_root[t[:4]] for t in company.tickers if t[:4] in by_root}
            if not matched:
                unresolved.append(company)
                continue
            # One company, one classification: the spreadsheet keys on the root,
            # so every class of it lands on the same row.
            classification = next(iter(matched.values()))
            for ticker in company.tickers:
                classifications[ticker] = classification
                from_sheet += 1

        detail, unclassified, unknown = await self._detail_fallback(
            unresolved, vocabulary
        )
        classifications.update(detail)
        return B3Fetch(
            classifications=classifications,
            from_sheet=from_sheet,
            from_detail=len(detail),
            unclassified=unclassified,
            unknown_labels=unknown,
        )

    async def _sheet(self) -> tuple[dict[str, Classification], frozenset[str]]:
        """Trading root -> classification, plus every label B3 publishes."""
        raw = await self._download_sheet()
        rows = await asyncio.to_thread(read_rows, raw)

        by_root: dict[str, Classification] = {}
        vocabulary: set[str] = set()
        setor = subsetor = segmento = ""
        for row in rows:
            setor = _cell(row, _COL_SETOR) or setor
            subsetor = _cell(row, _COL_SUBSETOR) or subsetor
            segmento = _cell(row, _COL_SEGMENTO) or segmento
            root = _cell(row, _COL_ROOT).upper()
            if not _ROOT.match(root) or not (setor and subsetor and segmento):
                continue  # a header, a spacer, or a footnote
            by_root[root] = Classification(setor, subsetor, segmento)
            vocabulary.update((setor, subsetor, segmento))
        return by_root, frozenset(vocabulary)

    async def _download_sheet(self) -> Path:
        payload = _encoded({"language": "pt-br", "pageNumber": 1, "pageSize": 100})
        url = f"{self._base_url}/GetDownloadIndustryClassification/{payload}"
        response = await self._http.get(
            url, headers={"User-Agent": _USER_AGENT}, timeout=60.0
        )
        response.raise_for_status()
        directory = self._cache_dir or Path(tempfile.gettempdir())
        directory.mkdir(parents=True, exist_ok=True)
        path = directory / "b3_industry_classification.xlsx"
        path.write_bytes(response.content)
        return path

    async def _detail_fallback(
        self, companies: list[ListedCompany], vocabulary: frozenset[str]
    ) -> tuple[dict[str, Classification], tuple[str, ...], tuple[str, ...]]:
        """Ask per registrant for the companies the spreadsheet cannot name."""
        resolved: dict[str, Classification] = {}
        unclassified: list[str] = []
        unknown: set[str] = set()

        for company in companies:
            raw = await self._industry(company.cd_cvm)
            if raw is None:
                unclassified.append(company.ticker)
                continue
            levels = [part.strip() for part in raw.split(_LEVEL_SEPARATOR)]
            if len(levels) != 3:
                logger.warning(
                    "B3 classification for %s has %d level(s): %r",
                    company.ticker,
                    len(levels),
                    raw,
                )
                unclassified.append(company.ticker)
                continue
            fixed = [LABEL_CORRECTIONS.get(level, level) for level in levels]
            # The spreadsheet is the authority on what a label may be, so a
            # fallback label outside its vocabulary is a change in the source.
            unknown.update(label for label in fixed if label not in vocabulary)
            classification = Classification(fixed[0], fixed[1], fixed[2])
            for ticker in company.tickers:
                resolved[ticker] = classification

        return resolved, tuple(unclassified), tuple(sorted(unknown))

    async def _industry(self, cd_cvm: str) -> str | None:
        """``industryClassification`` for one registrant, or ``None``."""
        payload = _encoded({"codeCVM": cd_cvm, "language": "pt-br"})
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


def _cell(row: list[str], index: int) -> str:
    return row[index].strip() if index < len(row) else ""


def _encoded(params: dict[str, object]) -> str:
    """B3's proxy takes its parameters as base64-encoded JSON in the path."""
    return base64.b64encode(json.dumps(params).encode()).decode()
