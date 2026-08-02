"""CotahistArchive and the B3 price providers: reduction, caching, freshness.

The fixture is twelve real records carved out of ``COTAHIST_A2015.ZIP`` — three
PETR4 sessions, two VALE3, one TAEE11, two CEGR3, plus the file's header and
trailer, an option on PETR and a forward-market line. The last two are there
because they are the trap: they carry the same ``TIPREG`` as a spot quote and
only the market type tells them apart. CEGR3 is the other trap: it is quoted per
lot of a thousand shares, so its record says 100.00 where the share is worth ten
centavos.
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
    B3BaseChanges,
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


def _archive_bytes(*, out_of_order: bool = False) -> bytes:
    import io

    payload = FIXTURE.read_bytes()
    if out_of_order:
        records = [record for record in payload.split(b"\r\n") if record]
        payload = b"\r\n".join(reversed(records)) + b"\r\n"
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("COTAHIST_A2015.TXT", payload)
    return buffer.getvalue()


def _write_archive(
    cache_dir: Path, year: int = 2015, *, out_of_order: bool = False
) -> Path:
    cache_dir.mkdir(parents=True, exist_ok=True)
    path = cache_dir / f"COTAHIST_A{year}.ZIP"
    path.write_bytes(_archive_bytes(out_of_order=out_of_order))
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

    assert set(year) == {"PETR4", "VALE3", "TAEE11", "CEGR3"}
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


async def test_a_lot_quoted_price_is_divided_by_its_quote_factor(
    tmp_path: Path,
) -> None:
    """CEGR3 was quoted per lot of a thousand shares, and the layout says so.

    Its 2015 records read 100.00 and 95.08; the share was worth ten centavos and
    nine and a half. Read at face value the price — and every multiple built on
    it — is off by three orders of magnitude.

    The record proves it against itself: the 29 May line moved 200,000 shares
    for R$19,016, which is R$0.09508 each, and not the R$95.08 the price field
    reads on its own.
    """
    _write_archive(tmp_path)
    archive, http = _archive(tmp_path)

    async with http:
        year = await archive.year(2015)

    cegr3 = year["CEGR3"]
    assert cegr3.sessions == 2
    assert cegr3.average == (Decimal("0.1") + Decimal("0.09508")) / 2
    assert cegr3.last_close == Decimal("0.09508")


async def test_the_lot_quoted_closes_survive_the_cached_reduction(
    tmp_path: Path,
) -> None:
    # A price finer than a centavo is what the reduction's old integer encoding
    # could not carry, so the round trip is the assertion, not the arithmetic.
    transport = _CountingTransport(_archive_bytes())
    archive, http = _archive(tmp_path, transport=transport)
    async with http:
        await archive.year(2015)

    (tmp_path / "COTAHIST_A2015.ZIP").unlink()
    reopened, http2 = _archive(tmp_path, transport=transport)
    async with http2:
        sessions = await B3PriceProvider(reopened).year_sessions("CEGR3", 2015)

    assert [(s.session, s.close) for s in sessions] == [
        (date(2015, 4, 14), Decimal("0.1")),
        (date(2015, 5, 29), Decimal("0.09508")),
    ]


async def test_the_sessions_are_ordered_by_date_not_by_file_position(
    tmp_path: Path,
) -> None:
    """The 2025 archive is not sorted by session; the 2024 one is.

    So the order is the reduction's to establish: ``session_closes`` promises
    oldest first, and the year's last close has to be its latest session rather
    than whichever record the file happened to end on.
    """
    _write_archive(tmp_path, out_of_order=True)
    archive, http = _archive(tmp_path)

    async with http:
        sessions = await B3PriceProvider(archive).year_sessions("PETR4", 2015)
        year = await archive.year(2015)

    assert [(s.session, s.close) for s in sessions] == [
        (date(2015, 1, 2), Decimal("9.36")),
        (date(2015, 1, 5), Decimal("8.61")),
        (date(2015, 1, 6), Decimal("8.33")),
    ]
    assert year["PETR4"].last_session == date(2015, 1, 6)
    assert year["PETR4"].last_close == Decimal("8.33")


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


def _quote(
    *,
    session: str,
    code: str,
    cents: int,
    distribution: str,
    marker: str = "",
) -> str:
    """One COTAHIST quote record, built field by field.

    Synthesised rather than carved out of the archive, and only for the rights
    tests: what they isolate is a *sequence* of markers and distribution numbers
    across sessions, which no handful of real lines shows in one place. Each case
    names the real one it mirrors. ESPECI is ten wide — four for the class, four
    for the "ex" marker, two for the governance segment.
    """
    especi = f"{'ON':<4}{marker:<4}{'':<2}"
    price = f"{cents:013d}"
    record = (
        "01"
        + session
        + "02"
        + f"{code:<12}"
        + "010"
        + f"{code[:4]:<12}"
        + especi
        + f"{'':<3}"
        + f"{'R$':<4}"
        + price * 5
        + price * 2
        + f"{1:05d}"
        + f"{100:018d}"
        + f"{cents:018d}"
        + f"{0:013d}"
        + "0"
        + "99991231"
        + f"{1:07d}"
        + f"{0:013d}"
        + f"{'BR' + code[:4] + 'ACNOR0':<12}"
        + distribution
    )
    assert len(record) == 245, len(record)
    return record


def _rights_archive(cache_dir: Path, year: int, records: list[str]) -> None:
    import io

    cache_dir.mkdir(parents=True, exist_ok=True)
    buffer = io.BytesIO()
    payload = "\r\n".join(records).encode("latin-1") + b"\r\n"
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr(f"COTAHIST_A{year}.TXT", payload)
    (cache_dir / f"COTAHIST_A{year}.ZIP").write_bytes(buffer.getvalue())


async def test_a_rights_change_marked_as_a_bonus_is_a_base_change(
    tmp_path: Path,
) -> None:
    """BBAS3's shape: the distribution number steps and the session trades ``EB``."""
    _rights_archive(
        tmp_path,
        2024,
        [
            _quote(session="20240415", code="BBAS3", cents=5646, distribution="321"),
            _quote(
                session="20240416",
                code="BBAS3",
                cents=2791,
                distribution="322",
                marker="EB",
            ),
        ],
    )
    archive, http = _archive(tmp_path, today=date(2025, 1, 2))

    async with http:
        changes = await B3BaseChanges(archive).base_changes("BBAS3", (2024,))

    assert [(c.session, round(c.ratio, 4)) for c in changes] == [
        (date(2024, 4, 16), Decimal("2.0229"))
    ]


async def test_a_dividend_moves_the_rights_state_without_moving_the_base(
    tmp_path: Path,
) -> None:
    # ``ED`` and ``EJ`` are cash leaving the company: the price drops and no share
    # is created. 253 of them moved a price more than 15% over the archives, and
    # every one would be a false action here.
    _rights_archive(
        tmp_path,
        2024,
        [
            _quote(session="20240219", code="BBAS3", cents=2435, distribution="249"),
            _quote(
                session="20240220",
                code="BBAS3",
                cents=2397,
                distribution="250",
                marker="ED",
            ),
        ],
    )
    archive, http = _archive(tmp_path, today=date(2025, 1, 2))

    async with http:
        assert await B3BaseChanges(archive).base_changes("BBAS3", (2024,)) == ()


async def test_a_sticky_marker_does_not_report_one_bonus_twice(
    tmp_path: Path,
) -> None:
    """Itaúsa 2022: the bonus went ex on 11 November and the interest on the 21st.

    The marker runs for about eight sessions, so the second date reads ``EJB``
    and its ``B`` belongs to the first. Read on its own it is a second bonus,
    and Itaúsa grows three actions it never took.
    """
    _rights_archive(
        tmp_path,
        2022,
        [
            _quote(session="20221110", code="ITSA4", cents=992, distribution="422"),
            _quote(
                session="20221111",
                code="ITSA4",
                cents=884,
                distribution="423",
                marker="EB",
            ),
            _quote(
                session="20221121",
                code="ITSA4",
                cents=887,
                distribution="424",
                marker="EJB",
            ),
        ],
    )
    archive, http = _archive(tmp_path, today=date(2023, 1, 2))

    async with http:
        changes = await B3BaseChanges(archive).base_changes("ITSA4", (2022,))

    assert [c.session for c in changes] == [date(2022, 11, 11)]


async def test_a_base_change_on_a_year_s_first_session_is_seen_against_december(
    tmp_path: Path,
) -> None:
    """SLCE3's bonus went ex on 3 January 2022, so the state it moved from is 2021's.

    Reading 2022 alone the first session has nothing to differ from, and the
    action is invisible — which is why the reader walks the years in order.
    """
    _rights_archive(
        tmp_path,
        2021,
        [_quote(session="20211230", code="SLCE3", cents=4380, distribution="118")],
    )
    _rights_archive(
        tmp_path,
        2022,
        [
            _quote(
                session="20220103",
                code="SLCE3",
                cents=4045,
                distribution="119",
                marker="EB",
            )
        ],
    )
    archive, http = _archive(tmp_path, today=date(2023, 1, 2))

    async with http:
        reader = B3BaseChanges(archive)
        alone = await reader.base_changes("SLCE3", (2022,))
        joined = await reader.base_changes("SLCE3", (2021, 2022))

    assert alone == ()
    assert [(c.session, round(c.ratio, 4)) for c in joined] == [
        (date(2022, 1, 3), Decimal("1.0828"))
    ]


async def test_an_event_named_a_session_after_it_is_dated_is_still_dated(
    tmp_path: Path,
) -> None:
    """Itaúsa's December 2025 bonus: the rights state steps under ``EX``.

    ``EB`` only appears the next session, on the same distribution number — so a
    reader looking at the opening session alone sees a marker it cannot classify
    and drops a real action. VIVT3's 2025 split-and-grupamento has the same
    shape.
    """
    _rights_archive(
        tmp_path,
        2025,
        [
            _quote(
                session="20251218",
                code="ITSA4",
                cents=1160,
                distribution="454",
                marker="EDJ",
            ),
            _quote(
                session="20251219",
                code="ITSA4",
                cents=1147,
                distribution="455",
                marker="EX",
            ),
            _quote(
                session="20251222",
                code="ITSA4",
                cents=1146,
                distribution="455",
                marker="EB",
            ),
        ],
    )
    archive, http = _archive(tmp_path, today=date(2026, 1, 2))

    async with http:
        changes = await B3BaseChanges(archive).base_changes("ITSA4", (2025,))

    # Dated by the session the state moved on, not by the one that named it.
    assert [c.session for c in changes] == [date(2025, 12, 19)]


async def test_a_bonus_following_a_bonus_is_a_second_action(tmp_path: Path) -> None:
    """SLC Agrícola paid one in May 2023 and another in December, back to back.

    Nothing moved the rights state in between, so the two are consecutive spans:
    measuring the second against the first's markers rather than against the
    session before it hides an action the market plainly repriced.
    """
    _rights_archive(
        tmp_path,
        2023,
        [
            _quote(session="20230508", code="SLCE3", cents=4400, distribution="120"),
            _quote(
                session="20230509",
                code="SLCE3",
                cents=4003,
                distribution="121",
                marker="EB",
            ),
            _quote(session="20231213", code="SLCE3", cents=4152, distribution="121"),
            _quote(
                session="20231214",
                code="SLCE3",
                cents=2000,
                distribution="122",
                marker="EB",
            ),
        ],
    )
    archive, http = _archive(tmp_path, today=date(2024, 1, 2))

    async with http:
        changes = await B3BaseChanges(archive).base_changes("SLCE3", (2023,))

    assert [c.session for c in changes] == [date(2023, 5, 9), date(2023, 12, 14)]
