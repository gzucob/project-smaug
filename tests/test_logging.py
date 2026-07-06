"""Logging setup keeps the brapi token out of the logs (public repo)."""

import logging

from smaug.shared.logging import configure_logging


def test_httpx_logger_is_muted_so_the_token_url_never_prints() -> None:
    configure_logging()
    # httpx logs the full request URL (with ?token=...) at INFO; must stay quiet.
    assert logging.getLogger("httpx").level >= logging.WARNING
