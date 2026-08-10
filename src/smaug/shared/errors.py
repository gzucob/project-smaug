"""Domain and infrastructure error hierarchy.

Kept in ``shared`` so every context raises from the same root, and the
entrypoints can catch a single base type when deciding how to fail.
"""

from __future__ import annotations


class SmaugError(Exception):
    """Base for every error raised by the application."""


class UnknownTickerError(SmaugError):
    """Ticker resolves nowhere in the CVM FCA registry — a user input error.

    Raised by the CLI's registry-backed resolvers so a typo (or a company CVM
    does not list) is reported as a single clean line instead of a raw lookup
    failure.
    """

    def __init__(self, ticker: str) -> None:
        self.ticker = ticker
        super().__init__(f"Unknown ticker: {ticker} (not in portfolio)")


class SourceError(SmaugError):
    """Base for failures while talking to a data source.

    Every source raises from this one family — CVM's archives and B3's files and
    endpoints alike — so the ingestion use case keeps a single error root to
    handle. It was called ``BrapiError`` while brapi was the first source; the
    name outlived the vendor by a year and was corrected when it was removed
    (ADR 0041).
    """


class SourceAuthError(SourceError):
    """Credentials missing or rejected (HTTP 401). The whole run must stop.

    No source needs a credential today (ADR 0041) — kept because the use case's
    "stop the run" branch is about the class of failure, not about who raises it.
    """


class SourceRateLimitError(SourceError):
    """The source is refusing the pace (HTTP 402/429). Back off / stop the run."""


class SourceNotFoundError(SourceError):
    """Ticker or module not found (HTTP 404). Skip this call, keep going."""


class SourceTimeoutError(SourceError):
    """Transport-layer failure before any HTTP response (timeout / connection).

    An httpx timeout or network error never reaches a status check — no response
    exists to inspect — so it would otherwise escape the ``SourceError`` family
    and crash the whole ``analyze`` run. Mapping it here lets the price call
    degrade to null market multiples per ticker.
    """


class SourceForbiddenError(SourceError):
    """The source refuses this call outright (HTTP 403). Skip it, keep going."""


class CvmDownloadError(SourceError):
    """The CVM yearly ZIP could not be downloaded (retries exhausted or 4xx).

    Fatal for the run: the ZIP is shared by every ticker of that year/document,
    so there is nothing left to collect once it is unavailable.
    """

    def __init__(
        self, message: str, *, quarantined_artifact_id: str | None = None
    ) -> None:
        self.quarantined_artifact_id = quarantined_artifact_id
        super().__init__(message)


class SourceBatchValidationError(SourceError):
    """A source batch failed a declared validation rule and was quarantined."""


class SourceUnexpectedStatusError(SourceError):
    """Any other non-success HTTP status we did not plan for."""

    def __init__(self, status_code: int, message: str) -> None:
        self.status_code = status_code
        super().__init__(message)
