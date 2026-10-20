"""Mirror registrant-verified B3 listing snapshots."""

from collections.abc import Mapping, Sequence
from datetime import UTC, datetime

import httpx

from smaug.ingestion.domain.ports import RawFetchResult
from smaug.ingestion.domain.runs import ParserIdentity
from smaug.ingestion.domain.validation import (
    BatchValidationReporter,
    SourceBatchValidation,
    ValidationFinding,
    ValidationRule,
)
from smaug.ingestion.infrastructure.b3_listed_company import (
    B3CompanyResolutionError,
    B3ListedCompanyResolver,
)
from smaug.ingestion.infrastructure.batch_validation import record_or_quarantine
from smaug.shared.errors import SourceNotFoundError

B3_LISTING_MODULE = "LISTING_B3"


class B3ListingSource:
    """Preserve B3's listing labels and their observation time."""

    source = "b3"
    parser_identity = ParserIdentity("b3.listing.json", 2)

    def __init__(
        self,
        http: httpx.AsyncClient,
        ticker_to_code: Mapping[str, str],
        ticker_to_cnpj: Mapping[str, str],
        *,
        base_url: str,
        validation_reporter: BatchValidationReporter | None = None,
    ) -> None:
        self._resolver = B3ListedCompanyResolver(http, base_url=base_url)
        self._codes = ticker_to_code
        self._cnpjs = ticker_to_cnpj
        self._validation_reporter = validation_reporter

    async def fetch(self, ticker: str, module: str) -> Sequence[RawFetchResult]:
        code = self._codes.get(ticker)
        if code is None:
            raise SourceNotFoundError(f"no CVM identity for {ticker}")
        try:
            company = await self._resolver.resolve_by_cvm(
                code, cnpj=self._cnpjs.get(ticker)
            )
        except B3CompanyResolutionError as exc:
            await record_or_quarantine(
                self._validation_reporter,
                SourceBatchValidation(
                    source=self.source,
                    batch=ticker[:4].upper(),
                    parser=self.parser_identity,
                    module=module,
                    rules=(
                        ValidationRule("response-schema", 1),
                        ValidationRule("coverage-established", 1),
                    ),
                    findings=(ValidationFinding(exc.code, exc.detail),),
                    evidence=exc.evidence,
                ),
            )
            raise AssertionError("a rejected identity must be quarantined") from exc
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
