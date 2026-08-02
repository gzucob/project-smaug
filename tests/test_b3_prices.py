"""CotahistArchive and the B3 price providers: reduction, caching, freshness.

The fixture is ten real records carved out of ``COTAHIST_A2015.ZIP`` — three
PETR4 sessions, two VALE3, one TAEE11, plus the file's header and trailer, an
option on PETR and a forward-market line. The last two are there because they
are the trap: they carry the same ``TIPREG`` as a spot quote and only the market
type tells them apart.
"""

from __future__ import annotations

import os
import zipfile
from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path

import httpx
import pytest

from smaug.analysis.infrastructure.b3_prices import (
    B3PriceProvider,
    B3QuoteProvider,
    CotahistArchive,
)

FIXTURE = Path(__file__).parent / "fixtures" / "cotahist_sample.txt"


async def no_sleep(_seconds: float) -> None:
    return None


class _CountingTransport(httpx.AsyncBaseTransport):
    """Serves the fixture archive and counts how often it was asked for it."""

    def __init__(self, archive_bytes: bytes) -> None:
        self.requests = 0
        self._bytes = archive_bytes

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        self.requests += 1
        return httpx.Response(200, content=self._bytes)


def _archive_bytes() -> bytes:
    import io

    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("COTAHIST_A2015.TXT", FIXTURE.read_bytes())
    return buffer.getvalue()


def _write_archive(cache_dir: Path, year: int = 2015) -> Path:
    cache_dir.mkdir(parents=True, exist_ok=True)
    path = cache_dir / f"COTAHIST_A{year}.ZIP"
    path.write_bytes(_archive_bytes())
    return path


def _archive(
    cache_dir: Path,
    *,
    today: date = date(2016, 3, 1),
    transport: httpx.AsyncBaseTransport | None = None,
) -> tuple[CotahistArchive, httpx.AsyncClient]:
    http = httpx.AsyncClient(transport=transport or _CountingTransport(b""))
    return (
        CotahistArchive(
            http,
            cache_dir=str(cache_dir),
            sleep=no_sleep,
            today=lambda: today,
        ),
        http,
    )


async def test_only_spot_market_records_survive_the_reduction(tmp_path: Path) -> None:
    _write_archive(tmp_path)
    archive, http = _archive(tmp_path)

    async with http:
        year = await archive.year(2015)

    assert set(year) == {"PETR4", "VALE3", "TAEE11"}
    # PETRA1 is an option and ABCB4F a forward — both are TIPREG "01" like a
    # spot quote, and averaging them into the share's price is the whole reason
    # the market-type filter exists.
    assert "PETRA1" not in year
    assert "ABCB4F" not in year


async def test_a_year_is_the_mean_of_its_closes_and_the_last_one(
    tmp_path: Path,
) -> None:
    _write_archive(tmp_path)
    archive, http = _archive(tmp_path)

    async with http:
        year = await archive.year(2015)

    petr4 = year["PETR4"]
    assert petr4.sessions == 3
    # 9.36 + 8.61 + 8.33, the three sessions in the fixture
    assert petr4.average == (Decimal("9.36") + Decimal("8.61") + Decimal("8.33")) / 3
    assert petr4.last_session == date(2015, 1, 6)
    assert petr4.last_close == Decimal("8.33")

    vale3 = year["VALE3"]
    assert vale3.sessions == 2
    assert vale3.average == Decimal("21.12")
    assert vale3.last_close == Decimal("20.96")


async def test_year_prices_carry_the_nominal_average_and_no_adjusted_one(
    tmp_path: Path,
) -> None:
    _write_archive(tmp_path)
    archive, http = _archive(tmp_path)

    async with http:
        prices = await B3PriceProvider(archive).year_prices("PETR4", 2015)

    assert (
        prices.nominal_avg == (Decimal("9.36") + Decimal("8.61") + Decimal("8.33")) / 3
    )
    # The published file carries the traded price and no dividend adjustment;
    # the total-return basis is rebuilt from corporate events, not read here.
    assert prices.adjusted_avg is None
    assert prices.null_reason is None


async def test_the_daily_closes_survive_the_reduction(tmp_path: Path) -> None:
    """A year is kept as its sessions too, not only as their mean.

    A corporate action falls on a day, so restating a year means restating the
    closes either side of it separately (ADR 0033) — which a mean has already
    thrown away.
    """
    _write_archive(tmp_path)
    archive, http = _archive(tmp_path)

    async with http:
        sessions = await B3PriceProvider(archive).year_sessions("PETR4", 2015)

    assert [(s.session, s.close) for s in sessions] == [
        (date(2015, 1, 2), Decimal("9.36")),
        (date(2015, 1, 5), Decimal("8.61")),
        (date(2015, 1, 6), Decimal("8.33")),
    ]


async def test_the_closes_come_back_off_the_cached_reduction(tmp_path: Path) -> None:
    # The reduction is what a second run reads; a series it did not persist is a
    # series that exists only on the run that built it.
    transport = _CountingTransport(_archive_bytes())
    archive, http = _archive(tmp_path, transport=transport)
    async with http:
        await archive.year(2015)

    (tmp_path / "COTAHIST_A2015.ZIP").unlink()
    reopened, http2 = _archive(tmp_path, transport=transport)
    async with http2:
        sessions = await B3PriceProvider(reopened).year_sessions("VALE3", 2015)

    assert [(s.session, s.close) for s in sessions] == [
        (date(2015, 1, 2), Decimal("21.28")),
        (date(2015, 1, 5), Decimal("20.96")),
    ]


async def test_a_code_with_no_session_that_year_has_no_series_either(
    tmp_path: Path,
) -> None:
    _write_archive(tmp_path)
    archive, http = _archive(tmp_path)

    async with http:
        assert await B3PriceProvider(archive).year_sessions("TAEE4", 2015) == ()


async def test_a_code_with_no_session_that_year_is_a_plain_null(
    tmp_path: Path,
) -> None:
    _write_archive(tmp_path)
    archive, http = _archive(tmp_path)

    async with http:
        prices = await B3PriceProvider(archive).year_prices("TAEE4", 2015)

    assert prices.nominal_avg is None
    # Not PRICE_SYMBOL_NOT_FOUND: B3 does not reject a symbol, it simply has no
    # record where nothing traded. TAEE4 was listed in 2015 and did not trade a
    # single session — the fact Yahoo's absent series was reporting all along.
    assert prices.null_reason is None


async def test_the_quote_is_the_last_close_of_the_running_year(
    tmp_path: Path,
) -> None:
    _write_archive(tmp_path)
    archive, http = _archive(tmp_path, today=date(2015, 6, 1))

    async with http:
        quote = await B3QuoteProvider(archive, today=lambda: date(2015, 6, 1)).get(
            "VALE3"
        )

    assert quote.price == Decimal("20.96")  # its last session, not its average
    # The cap is summed by the use case from the CVM's filed counts (ADR 0014),
    # so a quote source never volunteers a market cap of its own.
    assert quote.market_cap is None


async def test_a_code_absent_from_the_running_year_has_no_quote(
    tmp_path: Path,
) -> None:
    _write_archive(tmp_path)
    archive, http = _archive(tmp_path, today=date(2015, 6, 1))

    async with http:
        quote = await B3QuoteProvider(archive, today=lambda: date(2015, 6, 1)).get(
            "RDNI3"
        )

    assert quote.price is None


async def test_the_archive_is_downloaded_and_reduced_once_per_run(
    tmp_path: Path,
) -> None:
    transport = _CountingTransport(_archive_bytes())
    archive, http = _archive(tmp_path, transport=transport)

    async with http:
        first = await archive.year(2015)
        second = await archive.year(2015)

    assert transport.requests == 1  # the second caller got the memoized reduction
    assert first is second


async def test_a_closed_year_is_served_from_the_reduction_without_the_archive(
    tmp_path: Path,
) -> None:
    transport = _CountingTransport(_archive_bytes())
    archive, http = _archive(tmp_path, transport=transport)
    async with http:
        await archive.year(2015)

    # A closed year can never change, so a later run must not need the 85 MB ZIP
    # again — the reduction beside it is the whole point of writing one.
    (tmp_path / "COTAHIST_A2015.ZIP").unlink()
    reopened, http2 = _archive(tmp_path, transport=transport)
    async with http2:
        year = await reopened.year(2015)

    assert year["PETR4"].sessions == 3
    assert transport.requests == 1  # nothing was fetched the second time


async def test_the_running_years_reduction_is_rebuilt_when_it_predates_today(
    tmp_path: Path,
) -> None:
    transport = _CountingTransport(_archive_bytes())
    # 2015 is the *running* year here, so yesterday's copy is missing every
    # session traded since — it is replaced rather than served.
    stale, http = _archive(tmp_path, today=date(2015, 6, 1), transport=transport)
    async with http:
        await stale.year(2015)

    # Age both cached artifacts to the day they would really have been written.
    # Without this the archive keeps the test run's own mtime, which is years
    # after the injected "today" and would read as fresher than it is.
    downloaded_on = datetime(2015, 6, 1, tzinfo=UTC).timestamp()
    os.utime(tmp_path / "COTAHIST_A2015.ZIP", (downloaded_on, downloaded_on))

    fresher, http2 = _archive(tmp_path, today=date(2015, 6, 2), transport=transport)
    async with http2:
        year = await fresher.year(2015)

    assert transport.requests == 2
    assert year["PETR4"].sessions == 3  # and the rebuild still parses


async def test_an_archive_without_a_text_member_fails_loudly(tmp_path: Path) -> None:
    import io

    tmp_path.mkdir(parents=True, exist_ok=True)
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as empty:
        empty.writestr("readme.md", "not a quote series")
    (tmp_path / "COTAHIST_A2015.ZIP").write_bytes(buffer.getvalue())

    archive, http = _archive(tmp_path)
    with pytest.raises(ValueError, match="no .TXT member"):
        async with http:
            await archive.year(2015)
