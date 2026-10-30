"""Discover published annual archives from CVM's public directory indexes."""

import re
from html.parser import HTMLParser
from urllib.parse import urljoin

import httpx

from smaug.shared.errors import SourceMalformedError

CVM_DOCUMENT_ROOT = "https://dados.cvm.gov.br/dados/CIA_ABERTA/DOC"


class _Links(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.hrefs: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag == "a":
            self.hrefs.extend(v for k, v in attrs if k == "href" and v is not None)


class CvmArchiveCatalog:
    """Select actual published files, never guess an unpublished current year."""

    def __init__(self, http: httpx.AsyncClient) -> None:
        self._http = http

    async def discover(self) -> dict[str, tuple[int, ...]]:
        published: dict[str, tuple[int, ...]] = {}
        for dataset in ("DFP", "ITR", "FRE"):
            directory = f"{CVM_DOCUMENT_ROOT}/{dataset}/DADOS/"
            response = await self._http.get(directory)
            response.raise_for_status()
            links = _Links()
            links.feed(response.text)
            pattern = re.compile(
                re.escape(directory) + rf"{dataset.lower()}_cia_aberta_(\d{{4}})\.zip"
            )
            years = {
                int(match[1])
                for href in links.hrefs
                if (match := pattern.fullmatch(urljoin(directory, href)))
            }
            if not years:
                raise SourceMalformedError(
                    f"CVM {dataset}: no published archives found"
                )
            published[dataset] = tuple(sorted(years))
        return published
