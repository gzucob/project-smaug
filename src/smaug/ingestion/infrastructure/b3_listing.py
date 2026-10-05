"""Mirror registrant-verified B3 listing snapshots."""

from collections.abc import Mapping, Sequence
from datetime import UTC, datetime

import httpx

from smaug.ingestion.domain.ports import RawFetchResult
from smaug.ingestion.domain.runs import ParserIdentity
from smaug.ingestion.infrastructure.b3_listed_company import (
    B3CompanyResolutionError,
    B3ListedCompanyResolver,
)
from smaug.shared.errors import SourceMalformedError, SourceNotFoundError

B3_LISTING_MODULE = "LISTING_B3"


class B3ListingSource:
    """Preserve B3's listing labels and their observation time."""

    source = "b3"
    parser_identity = ParserIdentity("b3.listing.json", 1)

    def __init__(
        self,
        http: httpx.AsyncClient,
        ticker_to_code: Mapping[str, str],
        ticker_to_cnpj: Mapping[str, str],
        *,
        base_url: str,
    ) -> None:
        self._resolver = B3ListedCompanyResolver(http, base_url=base_url)
        self._codes = ticker_to_code
        self._cnpjs = ticker_to_cnpj

    async def fetch(self, ticker: str, module: str) -> Sequence[RawFetchResult]:
        code = self._codes.get(ticker)
        if code is None:
            raise SourceNotFoundError(f"no CVM identity for {ticker}")
        try:
            company = await self._resolver.resolve_by_cvm(
                code, cnpj=self._cnpjs.get(ticker)
            )
        except B3CompanyResolutionError as exc:
            raise SourceMalformedError(exc.detail) from exc
        observed = datetime.now(UTC).isoformat()
        return [
            RawFetchResult(
                module=module,
                source=self.source,
                http_status=200,
                cvm_code=code,
                request={"codeCVM": code, "observed_at": observed},
                payload={
                    "observed_at": observed,
                    "detail": dict(company.detail or {}),
                    "supplement": dict(company.supplement),
                },
            )
        ]
