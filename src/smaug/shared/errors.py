"""Domain and infrastructure error hierarchy.

Kept in ``shared`` so every context raises from the same root, and the
entrypoints can catch a single base type when deciding how to fail.
"""

from __future__ import annotations


class SmaugError(Exception):
    """Base for every error raised by the application."""


class BrapiError(SmaugError):
    """Base for failures while talking to the brapi API."""


class BrapiAuthError(BrapiError):
    """Token is missing/invalid (HTTP 401). The whole run must stop."""


class BrapiRateLimitError(BrapiError):
    """Plan limit exceeded (HTTP 402/429). Back off / stop the run."""


class BrapiNotFoundError(BrapiError):
    """Ticker or module not found (HTTP 404). Skip this call, keep going."""


class BrapiForbiddenError(BrapiError):
    """Ticker requires a higher brapi plan (HTTP 403). Skip this call."""


class BrapiUnexpectedStatusError(BrapiError):
    """Any other non-success HTTP status we did not plan for."""

    def __init__(self, status_code: int, message: str) -> None:
        self.status_code = status_code
        super().__init__(message)
