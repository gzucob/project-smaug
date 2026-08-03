"""Logging setup keeps the per-request chatter out of the collection log."""

import logging

from smaug.shared.logging import configure_logging


def test_httpx_logger_is_muted_so_the_run_log_stays_readable() -> None:
    configure_logging()
    # httpx logs the full request line at INFO, and a whole-exchange run makes
    # thousands of them; the collection log is what is meant to be read.
    assert logging.getLogger("httpx").level >= logging.WARNING
