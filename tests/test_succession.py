"""A security's price under every code it has traded as (#193, ADR 0042).

The fixtures are the measured cases, kept to their real shape: Arezzo renaming
after a merger it survived (a seam the price crosses), Rumo inheriting ALL's
registrant (a seam it does not), Le Lis Blanc grupamenting on the day it renamed
(a seam the tape says nothing about), and an illiquid class that simply trades
five days a year.
"""

from __future__ import annotations

import csv
import io
import json
import zipfile
from collections.abc import Mapping, Sequence
from datetime import date, timedelta
from decimal import Decimal
from pathlib import Path

import httpx

from smaug.analysis.domain.capital import RestatementStep
from smaug.analysis.domain.financials import MarketData
from smaug.analysis.domain.indicators import NullReason
from smaug.analysis.domain.succession import (
    CodeWindow,
    chain,
    joined,
    joins,
    structural_gap,
)
from smaug.analysis.infrastructure.b3_prices import B3BaseChanges, YearQuotes
from smaug.analysis.infrastructure.succession import (
    CodeSuccession,
    SuccessionPriceProvider,
)
from smaug.portfolio.domain.securities import confirms_name, name_key
from smaug.portfolio.domain.share_classes import TickerCodeEvidence
from smaug.portfolio.infrastructure.cvm_securities import CvmSecurityHistory

TODAY = date(2026, 6, 1)


def _window(
    code: str,
    first: date,
    last: date,
    *,
    first_close: str = "10",
    last_close: str = "10",
) -> CodeWindow:
    return CodeWindow(
        code=code,
        first_session=first,
        last_session=last,
        first_close=Decimal(first_close),
        last_close=Decimal(last_close),
    )


def _sessions(start: date, prices: Sequence[str]) -> list[tuple[date, Decimal]]:
    """Consecutive sessions from ``start``, one per day."""
    return [
        (start + timedelta(days=offset), Decimal(price))
        for offset, price in enumerate(prices)
    ]


def _quotes(
    sessions: Sequence[tuple[date, Decimal]],
    rights: Sequence[tuple[date, str, str]] = (),
    name: str = "",
    especi: str = "",
    identities: str = "",
) -> YearQuotes:
    january = date(sessions[0][0].year, 1, 1).toordinal()
    closes = " ".join(
        f"{session.toordinal() - january} {price}" for session, price in sessions
    )
    encoded = " ".join(
        f"{session.toordinal() - january} {distribution} {marker or '-'}"
        for session, distribution, marker in rights
    )
    prices = [price for _, price in sessions]
    return YearQuotes(
        sessions=len(sessions),
        average=sum(prices, Decimal(0)) / len(prices),
        last_session=sessions[-1][0],
        last_close=prices[-1],
        closes=closes,
        rights=encoded,
        name=name,
        especi=especi,
        identities=identities,
    )


class _FakeArchive:
    """Stands in for ``CotahistArchive``: reduced years, already in memory."""

    def __init__(self, years: Mapping[int, Mapping[str, YearQuotes]]) -> None:
        self._years = years

    async def year(self, year: int) -> Mapping[str, YearQuotes]:
        return self._years.get(year, {})


class _StaticPrices:
    """The inner ``SessionPriceProvider``: one code, its own year, nothing joined."""

    def __init__(self, archive: _FakeArchive) -> None:
        self._archive = archive

    async def get(self, ticker: str):  # noqa: ANN201 - only the year API is exercised
        quotes = (await self._archive.year(TODAY.year)).get(ticker)
        return MarketData() if quotes is None else MarketData(price=quotes.last_close)

    async def year_sessions(self, ticker: str, year: int):  # noqa: ANN201
        quotes = (await self._archive.year(year)).get(ticker)
        return () if quotes is None else quotes.session_closes()

    async def year_prices(self, ticker: str, year: int):  # noqa: ANN201
        from smaug.analysis.domain.financials import YearPrices

        quotes = (await self._archive.year(year)).get(ticker)
        if quotes is None:
            return YearPrices()
        return YearPrices(
            nominal_avg=quotes.average,
            closing=quotes.last_close,
            closing_session=quotes.last_session,
        )


# --- the decision -----------------------------------------------------------


def test_a_rename_joins_because_the_price_crosses_the_seam() -> None:
    # ARZZ3 closed 2024-07-31 at 48.65; AZZA3 opened the next session at 50.39.
    old = _window("ARZZ3", date(2015, 1, 2), date(2024, 7, 31), last_close="48.65")
    new = _window("AZZA3", date(2024, 8, 1), date(2026, 5, 29), first_close="50.39")

    assert joins(old, new)
    assert chain(new, [old], listed_since=date(2011, 2, 2)) == (old, new)


def test_a_share_exchange_does_not_join_even_under_one_registrant() -> None:
    # RAIL3's registrant is ALL's old CNPJ, so ALLL3 is a sibling by cadastre —
    # but ALLL3 closed at 3.97 and RUMO3 opened at 1.36 the next session.
    exchanged = _window("ALLL3", date(2015, 1, 2), date(2015, 3, 31), last_close="3.97")
    survivor = _window("RUMO3", date(2015, 4, 1), date(2017, 3, 10), first_close="1.36")

    assert not joins(exchanged, survivor)
    assert chain(survivor, [exchanged], listed_since=None) == (survivor,)


def test_the_listing_floor_keeps_a_pre_merger_code_out() -> None:
    exchanged = _window("ALLL3", date(2015, 1, 2), date(2015, 3, 31), last_close="3.97")
    survivor = _window("RUMO3", date(2015, 4, 1), date(2017, 3, 10), first_close="3.97")

    # Even with the seam continuous, the FCA dates the security from the day the
    # combination closed — everything before it is another share.
    assert chain(survivor, [exchanged], listed_since=date(2015, 4, 1)) == (survivor,)


def test_a_grupamento_on_the_seam_stops_the_chain() -> None:
    # LLIS3 closed 2023-02-08 at 1.73; VSTE3 opened at 12.93 — the tape marks
    # nothing, so joining would average two share bases into one year.
    old = _window("LLIS3", date(2015, 1, 2), date(2023, 2, 8), last_close="1.73")
    new = _window("VSTE3", date(2023, 2, 9), date(2026, 5, 29), first_close="12.93")

    assert not joins(old, new)
    assert chain(new, [old], listed_since=date(2008, 4, 28)) == (new,)


def test_a_concurrent_code_is_two_listings_not_a_succession() -> None:
    concurrent = _window("EMBR3", date(2015, 1, 2), date(2026, 5, 29))
    served = _window("EMBJ3", date(2025, 11, 3), date(2026, 5, 29))

    assert not joins(concurrent, served)
    assert chain(served, [concurrent], listed_since=None) == (served,)


def test_the_chain_walks_back_through_more_than_one_seam() -> None:
    oldest = _window("SSBR3", date(2015, 1, 2), date(2019, 8, 5), last_close="34.63")
    middle = _window(
        "ALSO3",
        date(2019, 8, 6),
        date(2023, 10, 24),
        first_close="35.00",
        last_close="23.33",
    )
    served = _window(
        "ALOS3", date(2023, 10, 25), date(2026, 5, 29), first_close="23.15"
    )

    assert chain(served, [oldest, middle], listed_since=date(2011, 2, 2)) == (
        oldest,
        middle,
        served,
    )


# --- the years the chain cannot name ----------------------------------------


def test_a_year_before_everything_the_chain_names_is_structural() -> None:
    assert structural_gap(
        2015,
        chain_start=date(2024, 8, 1),
        listed_since=date(2011, 2, 2),
        year_opened_on=date(2015, 1, 2),
        coverage=None,
    )


def test_the_year_of_the_change_is_structural_when_the_code_debuts_in_it() -> None:
    # COGN3 printed 53 of the 53 sessions left in 2019 — a debut, not a thin year.
    assert structural_gap(
        2019,
        chain_start=date(2019, 10, 11),
        listed_since=date(2007, 7, 23),
        year_opened_on=date(2019, 1, 2),
        coverage=Decimal(1),
    )


def test_an_illiquid_class_keeps_its_thin_year() -> None:
    # AHEB5 printed 5 sessions across 2016; its first one is not a debut.
    assert not structural_gap(
        2016,
        chain_start=date(2016, 5, 20),
        listed_since=date(2014, 4, 30),
        year_opened_on=date(2016, 1, 4),
        coverage=Decimal("0.03"),
    )


def test_a_security_younger_than_the_year_is_never_structural() -> None:
    assert not structural_gap(
        2021,
        chain_start=date(2021, 4, 29),
        listed_since=date(2021, 4, 29),
        year_opened_on=date(2021, 1, 4),
        coverage=Decimal(1),
    )


def test_a_code_that_never_traded_keeps_the_plain_missing_price() -> None:
    # BAUH3 has been listed since before the archive and has never printed a
    # session: a fact about the market, not a code we failed to name (#164).
    assert not structural_gap(
        2020,
        chain_start=None,
        listed_since=date(1970, 1, 1),
        year_opened_on=date(2020, 1, 2),
        coverage=None,
    )


# --- reading the archive under the chain ------------------------------------


def _renamed_archive() -> _FakeArchive:
    """ARZZ3 through July 2024, AZZA3 from August — the year they share."""
    return _FakeArchive(
        {
            2023: {"ARZZ3": _quotes(_sessions(date(2023, 1, 2), ["70", "72"]))},
            2024: {
                "ARZZ3": _quotes(_sessions(date(2024, 7, 30), ["48", "48.65"])),
                "AZZA3": _quotes(_sessions(date(2024, 8, 1), ["50.39", "51"])),
            },
            2025: {"AZZA3": _quotes(_sessions(date(2025, 1, 2), ["60", "62"]))},
        }
    )


def _nothing_explains(predecessor: CodeWindow, successor: CodeWindow) -> bool:
    return False


def _succession(archive: _FakeArchive) -> CodeSuccession:
    return CodeSuccession(
        archive,  # type: ignore[arg-type]  # a reduced archive is all it reads
        siblings=lambda ticker: ("ARZZ3",) if ticker == "AZZA3" else (),
        listed_since=lambda ticker: date(2011, 2, 2),
        today=lambda: TODAY,
    )


async def test_the_year_of_the_rename_is_read_under_both_codes() -> None:
    succession = _succession(_renamed_archive())

    assert await succession.candidates("AZZA3", 2023) == ("ARZZ3", "AZZA3")
    resolved = await succession.chain("AZZA3", 2023, explains=_nothing_explains)
    sessions = await succession.sessions(resolved, 2024)

    assert [close.session.day for close in sessions] == [30, 31, 1, 2]
    assert sessions[0].close == Decimal("48")


async def test_the_rename_year_averages_the_whole_year_not_its_tail() -> None:
    archive = _renamed_archive()
    provider = SuccessionPriceProvider(
        _StaticPrices(archive),  # type: ignore[arg-type]
        _succession(archive),
    )

    prices = await provider.year_prices("AZZA3", 2024)

    # Served today from AZZA3 alone, this year would average 50.695.
    expected = (Decimal("48") + Decimal("48.65") + Decimal("50.39") + Decimal("51")) / 4
    assert prices.nominal_avg == expected
    assert prices.closing == Decimal("51")
    assert prices.closing_session == date(2024, 8, 2)
    assert prices.null_reason is None


async def test_a_year_before_the_predecessor_reads_the_predecessor() -> None:
    archive = _renamed_archive()
    provider = SuccessionPriceProvider(
        _StaticPrices(archive),  # type: ignore[arg-type]
        _succession(archive),
    )

    prices = await provider.year_prices("AZZA3", 2023)

    assert prices.nominal_avg == Decimal("71")


async def test_a_code_with_no_sibling_is_delegated_untouched() -> None:
    archive = _FakeArchive(
        {2024: {"PETR4": _quotes(_sessions(date(2024, 1, 2), ["36", "38"]))}}
    )
    succession = CodeSuccession(
        archive,  # type: ignore[arg-type]
        listed_since=lambda ticker: date(1977, 7, 20),
        today=lambda: TODAY,
    )
    provider = SuccessionPriceProvider(_StaticPrices(archive), succession)  # type: ignore[arg-type]

    prices = await provider.year_prices("PETR4", 2024)

    assert prices.nominal_avg == Decimal("37")


async def test_the_current_quote_follows_a_proven_forward_successor() -> None:
    archive = _renamed_archive()
    archive._years[TODAY.year] = {  # type: ignore[index]
        "ARZZ3": _quotes(_sessions(date(2026, 1, 1), ["69"])),
        "AZZA3": _quotes(_sessions(date(2026, 1, 2), ["70", "71"])),
    }
    succession = CodeSuccession(
        archive,  # type: ignore[arg-type]
        siblings=lambda ticker: (
            ("AZZA3",) if ticker == "ARZZ3" else ("ARZZ3",) if ticker == "AZZA3" else ()
        ),
        listed_since=lambda ticker: date(2011, 2, 2),
        today=lambda: TODAY,
    )
    provider = SuccessionPriceProvider(
        _StaticPrices(archive),  # type: ignore[arg-type]
        succession,
    )

    quote = await provider.get("ARZZ3")

    assert quote.price == Decimal("71")


async def test_an_unproven_forward_change_keeps_the_current_price_missing() -> None:
    archive = _FakeArchive(
        {
            2025: {"NINJ3": _quotes(_sessions(date(2025, 12, 29), ["10"]))},
            2026: {"ARND3": _quotes(_sessions(date(2026, 1, 2), ["100"]))},
        }
    )
    succession = CodeSuccession(
        archive,  # type: ignore[arg-type]
        # A registrant/root claim is not enough. The seam's price does not carry.
        siblings=lambda ticker: ("ARND3",) if ticker == "NINJ3" else ("NINJ3",),
        today=lambda: TODAY,
    )
    provider = SuccessionPriceProvider(
        _StaticPrices(archive),  # type: ignore[arg-type]
        succession,
    )

    quote = await provider.get("NINJ3")

    assert quote.price is None
    assert quote.price_null_reason is None


async def test_a_simultaneous_class_cannot_supply_the_current_quote() -> None:
    archive = _FakeArchive(
        {
            2025: {"NINJ3": _quotes(_sessions(date(2025, 12, 29), ["10"]))},
            2026: {"ARND4": _quotes(_sessions(date(2026, 1, 2), ["10"]))},
        }
    )
    succession = CodeSuccession(
        archive,  # type: ignore[arg-type]
        # The fake resolver intentionally offers a different class. The
        # succession must enforce the exact class suffix at this boundary too.
        siblings=lambda ticker: ("ARND4",) if ticker == "NINJ3" else ("NINJ3",),
        today=lambda: TODAY,
    )
    provider = SuccessionPriceProvider(
        _StaticPrices(archive),  # type: ignore[arg-type]
        succession,
    )

    quote = await provider.get("NINJ3")

    assert quote.price is None


async def test_a_year_the_chain_cannot_name_is_a_named_null() -> None:
    # COGN3's shape: the code debuts mid-2019 and the FCA never names KROT3.
    archive = _FakeArchive(
        {
            2018: {"OTHR3": _quotes(_sessions(date(2018, 1, 2), ["10", "11"]))},
            2019: {
                "OTHR3": _quotes(_sessions(date(2019, 1, 2), ["10", "11"])),
                "COGN3": _quotes(_sessions(date(2019, 1, 3), ["10", "11"])),
            },
        }
    )
    succession = CodeSuccession(
        archive,  # type: ignore[arg-type]
        listed_since=lambda ticker: date(2007, 7, 23),
        today=lambda: date(2019, 12, 31),
    )
    provider = SuccessionPriceProvider(_StaticPrices(archive), succession)  # type: ignore[arg-type]

    prices = await provider.year_prices("COGN3", 2018)

    assert prices.nominal_avg is None
    assert prices.null_reason is NullReason.PRICE_SYMBOL_NOT_FOUND


# --- the tape the actions are dated off -------------------------------------


async def test_an_action_filed_under_the_earlier_code_is_still_dated() -> None:
    """A bonus under ARZZ3 has to survive the rename, or the joined sessions it
    precedes are never restated and the year mixes two share bases."""
    archive = _FakeArchive(
        {
            2023: {
                "ARZZ3": _quotes(
                    _sessions(date(2023, 1, 2), ["80", "40", "41"]),
                    rights=[
                        (date(2023, 1, 2), "144", ""),
                        (date(2023, 1, 3), "145", "EB"),
                    ],
                )
            },
            2024: {"AZZA3": _quotes(_sessions(date(2024, 8, 1), ["50", "51"]))},
        }
    )
    succession = _succession(archive)

    blind = await B3BaseChanges(archive, lambda: TODAY).base_changes(  # type: ignore[arg-type]
        "AZZA3", [2023, 2024]
    )
    joined = await B3BaseChanges(
        archive,  # type: ignore[arg-type]
        lambda: TODAY,
        codes=succession.candidates,
    ).base_changes("AZZA3", [2023, 2024])

    assert blind == ()
    assert [(change.session, change.ratio) for change in joined] == [
        (date(2023, 1, 3), Decimal("80") / Decimal("40"))
    ]


async def test_tape_predecessor_uses_explicit_especi_class_when_available() -> None:
    """The tape's class field outranks an inconsistent ticker suffix."""
    archive = _FakeArchive(
        {
            2022: {
                "OLDD3": _quotes(
                    _sessions(date(2022, 12, 30), ["10"]),
                    name="LEGACY",
                    especi="ON      N1",
                )
            },
            # ``HEAA4`` intentionally has an ordinary-share ESPECI despite its
            # preferred-looking suffix.  The explicit B3 field is the evidence
            # used to compare it with the predecessor.
            2023: {
                "HEAA4": _quotes(
                    _sessions(date(2023, 1, 2), ["10"]),
                    name="CURRENT",
                    especi="ON      N1",
                )
            },
        }
    )
    succession = CodeSuccession(
        archive,  # type: ignore[arg-type]
        names=lambda ticker: frozenset({"LEGACY"}),
        today=lambda: date(2023, 12, 31),
    )

    assert await succession.candidates("HEAA4", 2023) == ("OLDD3", "HEAA4")


async def test_tape_predecessor_uses_identity_at_the_head_session() -> None:
    """A code-year's first class is not evidence for a later seam session."""
    head_identities = json.dumps(
        [[1, "BRHEAAACNOR1", "ON      N1", "02", "CURRENT"]],
        separators=(",", ":"),
    )
    archive = _FakeArchive(
        {
            2022: {
                "OLDD3": _quotes(
                    _sessions(date(2022, 12, 30), ["10"]),
                    name="LEGACY",
                    especi="ON      N1",
                )
            },
            2023: {
                # The scalar value is an incomplete/historical identity; the
                # session-scoped evidence is the ordinary-share class.
                "HEAA4": _quotes(
                    _sessions(date(2023, 1, 2), ["10"]),
                    name="CURRENT",
                    especi="PN      N1",
                    identities=head_identities,
                )
            },
        }
    )
    succession = CodeSuccession(
        archive,  # type: ignore[arg-type]
        names=lambda ticker: frozenset({"LEGACY"}),
        today=lambda: date(2023, 12, 31),
    )

    assert await succession.candidates("HEAA4", 2023) == ("OLDD3", "HEAA4")


# --- the codes a registrant has filed ---------------------------------------


def _csv(header: Sequence[str], rows: Sequence[Sequence[str]]) -> bytes:
    buffer = io.StringIO()
    writer = csv.writer(buffer, delimiter=";", lineterminator="\n")
    writer.writerow(header)
    writer.writerows(rows)
    return buffer.getvalue().encode("latin-1")


def _fca_archive(
    path: Path,
    year: int,
    rows: Sequence[Sequence[str]],
    general: Sequence[Sequence[str]] = (),
) -> None:
    with zipfile.ZipFile(path / f"fca_cia_aberta_{year}.zip", "w") as archive:
        archive.writestr(
            f"fca_cia_aberta_valor_mobiliario_{year}.csv",
            _csv(
                ["CNPJ_Companhia", "Codigo_Negociacao", "Valor_Mobiliario", "Versao"],
                rows,
            ),
        )
        archive.writestr(
            f"fca_cia_aberta_geral_{year}.csv",
            _csv(
                ["CNPJ_Companhia", "Nome_Empresarial", "Versao"],
                [[*row, "1"] for row in general],
            ),
        )


async def test_the_codes_of_one_class_are_gathered_across_fca_years(
    tmp_path: Path,
) -> None:
    _fca_archive(
        tmp_path,
        2018,
        [
            ["16.590.234/0001-76", "ARZZ3", "Ações Ordinárias", "1"],
            ["05.878.397/0001-32", "ALSO3", "Ações Ordinárias", "1"],
            ["16.590.234/0001-76", "ARZZ11", "Units", "1"],
        ],
    )
    _fca_archive(
        tmp_path,
        2019,
        [
            ["16.590.234/0001-76", "AZZA3", "Ações Ordinárias", "1"],
            ["16.590.234/0001-76", "AZZA4", "Ações Preferenciais", "1"],
        ],
    )

    async with httpx.AsyncClient() as http:
        history = CvmSecurityHistory(
            http, through=2019, since=2018, cache_dir=str(tmp_path)
        )
        siblings = await history.resolver()

    assert siblings("AZZA3") == ("ARZZ3",)
    assert siblings("ARZZ3") == ("AZZA3",)
    # A different class of the same registrant is a different share, and a unit
    # is a different base again — neither is a predecessor.
    assert siblings("AZZA4") == ()
    assert siblings("ARZZ11") == ()
    # A registrant with one code has nothing to join.
    assert siblings("ALSO3") == ()


async def test_historical_code_evidence_keeps_the_fca_years(
    tmp_path: Path,
) -> None:
    _fca_archive(
        tmp_path,
        2018,
        [["16.590.234/0001-76", "ARZZ3", "Ações Ordinárias", "1"]],
    )
    _fca_archive(
        tmp_path,
        2019,
        [["16.590.234/0001-76", "AZZA3", "Ações Ordinárias", "1"]],
    )

    async with httpx.AsyncClient() as http:
        history = CvmSecurityHistory(
            http, through=2019, since=2018, cache_dir=str(tmp_path)
        )
        codes = await history.historical_codes()

    assert codes("AZZA3") == (
        TickerCodeEvidence("ARZZ3", (2018,)),
        TickerCodeEvidence("AZZA3", (2019,)),
    )


async def test_the_names_a_registrant_filed_come_from_every_year(
    tmp_path: Path,
) -> None:
    # The code column starts in 2018, but the names go back to 2010 — and it is
    # the early years that carry the old name (#198).
    _fca_archive(
        tmp_path,
        2015,
        [],
        general=[["16.590.234/0001-76", "AREZZO INDÚSTRIA E COMÉRCIO S.A."]],
    )
    _fca_archive(
        tmp_path,
        2019,
        [["16.590.234/0001-76", "AZZA3", "Ações Ordinárias", "1"]],
        general=[["16.590.234/0001-76", "AZZAS 2154 S.A."]],
    )
    # The history reader visits every year in the interval. Empty synthetic
    # archives keep this source-shaped unit test hermetic instead of making live
    # CVM calls for the years that carry no fact relevant to the assertion.
    for year in (2016, 2017, 2018):
        _fca_archive(tmp_path, year, [])

    async with httpx.AsyncClient() as http:
        history = CvmSecurityHistory(
            http, through=2019, since=2015, cache_dir=str(tmp_path)
        )
        names = await history.names()

    assert confirms_name(names("AZZA3"), "AREZZO CO")
    assert confirms_name(names("AZZA3"), "AZZAS 2154")
    assert not confirms_name(names("AZZA3"), "BROOKFIELD")


# --- the seam as the only witness to an action's date (#197, ADR 0043) -------


def _grupamento_archive() -> _FakeArchive:
    """Le Lis Blanc's shape: LLIS3 to 2023-02-08, VSTE3 from the 9th, x7.47 apart."""
    return _FakeArchive(
        {
            2022: {"LLIS3": _quotes(_sessions(date(2022, 12, 28), ["2.05", "2.09"]))},
            2023: {
                "LLIS3": _quotes(_sessions(date(2023, 2, 7), ["1.85", "1.73"])),
                "VSTE3": _quotes(_sessions(date(2023, 2, 9), ["12.93", "13.06"])),
                # The year's calendar comes from the code that traded most, and
                # the debut test needs one: VSTE3 printing every session left in
                # the year is what tells a rename from an illiquid share.
                "MRKT3": _quotes(_sessions(date(2023, 2, 7), ["9", "9", "9", "9"])),
            },
            2024: {"VSTE3": _quotes(_sessions(date(2024, 1, 2), ["20", "19"]))},
        }
    )


def _veste(archive: _FakeArchive) -> CodeSuccession:
    return CodeSuccession(
        archive,  # type: ignore[arg-type]
        siblings=lambda ticker: ("LLIS3",) if ticker == "VSTE3" else (),
        listed_since=lambda ticker: date(2008, 4, 28),
        today=lambda: date(2024, 6, 1),
    )


async def test_a_seam_the_price_does_not_cross_is_offered_as_a_date() -> None:
    archive = _grupamento_archive()
    changes = await B3BaseChanges(
        archive,  # type: ignore[arg-type]
        lambda: date(2024, 6, 1),
        codes=_veste(archive).candidates,
    ).base_changes("VSTE3", [2022, 2023, 2024])

    # B3's tape marks nothing here — the successor opens with a clean ESPECI and
    # a DISMES restarted at 100 — so the seam itself is the only witness.
    assert [(c.session, c.ratio) for c in changes] == [
        (date(2023, 2, 9), Decimal("1.73") / Decimal("12.93"))
    ]


async def test_a_seam_the_price_crosses_offers_nothing() -> None:
    archive = _renamed_archive()
    changes = await B3BaseChanges(
        archive,  # type: ignore[arg-type]
        lambda: TODAY,
        codes=_succession(archive).candidates,
    ).base_changes("AZZA3", [2023, 2024, 2025])

    # A rename on its own moves no share, and a candidate ratio of ~1 could only
    # be paired with some other action of a different date.
    assert changes == ()


def test_an_explained_seam_joins_and_an_unexplained_one_does_not() -> None:
    old = _window("LLIS3", date(2015, 1, 2), date(2023, 2, 8), last_close="1.73")
    new = _window("VSTE3", date(2023, 2, 9), date(2026, 5, 29), first_close="12.93")

    assert joined([old, new]) == (new,)
    assert joined([old, new], explains=lambda _p, _s: True) == (old, new)


async def test_the_price_joins_a_seam_the_restatement_has_dated() -> None:
    archive = _grupamento_archive()
    succession = _veste(archive)
    inner = _StaticPrices(archive)

    blind = SuccessionPriceProvider(inner, succession)  # type: ignore[arg-type]
    dated = SuccessionPriceProvider(
        inner,  # type: ignore[arg-type]
        succession,
        timeline=lambda ticker: _timeline(
            (date(2023, 2, 9), Decimal(106_073_983) / Decimal(848_591_865))
        ),
    )

    # Unexplained, the year is half a share base and is published as neither.
    assert (await blind.year_prices("VSTE3", 2023)).null_reason is (
        NullReason.PRICE_SYMBOL_NOT_FOUND
    )
    # Dated, the four sessions of 2023 are one series again — the two under LLIS3
    # are restated by the outer decorator, which is what the step is for.
    joined_year = await dated.year_prices("VSTE3", 2023)
    assert joined_year.null_reason is None
    assert (
        joined_year.nominal_avg
        == (Decimal("1.85") + Decimal("1.73") + Decimal("12.93") + Decimal("13.06")) / 4
    )


async def _timeline(*steps: tuple[date, Decimal]) -> tuple[RestatementStep, ...]:
    return tuple(RestatementStep(effective=day, ratio=ratio) for day, ratio in steps)


# --- the code the cadastre cannot name (#198, ADR 0044) ---------------------


def test_a_filed_name_confirms_what_b3_printed() -> None:
    filed = frozenset({name_key("TRACTEBEL ENERGIA SA"), name_key("ENGIE BRASIL S.A.")})

    assert confirms_name(filed, "TRACTEBEL")
    assert confirms_name(filed, "ENGIE")
    # Celpa was never called Brookfield, however adjacent the two tapes are.
    assert not confirms_name(filed, "BROOKFIELD")


def _retired_archive(name: str = "TRACTEBEL") -> _FakeArchive:
    """A code stops on one session and another starts on the very next."""
    return _FakeArchive(
        {
            2015: {
                "TBLE3": _quotes(_sessions(date(2015, 1, 2), ["30", "31"]), name=name),
                "MRKT3": _quotes(
                    _sessions(date(2015, 1, 2), ["9", "9"]), name="MARKET"
                ),
            },
            2016: {
                "TBLE3": _quotes(
                    _sessions(date(2016, 7, 19), ["40", "41.39"]), name=name
                ),
                "EGIE3": _quotes(
                    _sessions(date(2016, 7, 21), ["41.65", "42"]), name="ENGIE BRASIL"
                ),
                "MRKT3": _quotes(
                    _sessions(date(2016, 7, 19), ["9", "9", "9", "9"]), name="MARKET"
                ),
            },
        }
    )


def _tape_succession(archive: _FakeArchive, names: frozenset[str]) -> CodeSuccession:
    return CodeSuccession(
        archive,  # type: ignore[arg-type]
        siblings=lambda ticker: (),  # the cadastre names nothing: this is #198
        names=lambda ticker: names if ticker == "EGIE3" else frozenset(),
        listed_since=lambda ticker: date(1998, 1, 1),
        today=lambda: date(2016, 12, 31),
    )


async def test_the_tape_names_a_code_the_cadastre_never_did() -> None:
    filed = frozenset({name_key("TRACTEBEL ENERGIA SA")})
    succession = _tape_succession(_retired_archive(), filed)

    assert await succession.candidates("EGIE3", 2015) == ("TBLE3", "EGIE3")


async def test_tape_name_uses_identity_at_the_candidate_session() -> None:
    """A candidate's first NOMRES is not necessarily its seam identity."""
    identities = json.dumps(
        [
            [200, "BRTBLEACNOR1", "ON      N1", "02", "OLD NAME"],
            [201, "BRTBLEACNOR1", "ON      N1", "02", "TRACTEBEL"],
        ],
        separators=(",", ":"),
    )
    archive = _retired_archive()
    archive._years[2016]["TBLE3"] = _quotes(  # type: ignore[index]
        _sessions(date(2016, 7, 19), ["40", "41.39"]),
        name="OLD NAME",
        identities=identities,
    )
    succession = _tape_succession(
        archive, frozenset({name_key("TRACTEBEL ENERGIA SA")})
    )

    assert await succession.candidates("EGIE3", 2015) == ("TBLE3", "EGIE3")


async def test_the_tape_alone_is_not_enough_to_name_it() -> None:
    # Same adjacency, same price — but this registrant never filed that name.
    succession = _tape_succession(
        _retired_archive(), frozenset({name_key("ENGIE BRASIL S.A.")})
    )

    assert await succession.candidates("EGIE3", 2015) == ("EGIE3",)


async def test_a_price_that_does_not_carry_over_names_nothing() -> None:
    archive = _retired_archive()
    # The retiring code closes an eighth of where the new one opens.
    archive._years[2016]["TBLE3"] = _quotes(  # type: ignore[index]
        _sessions(date(2016, 7, 19), ["40", "5"]), name="TRACTEBEL"
    )
    succession = _tape_succession(archive, frozenset({name_key("TRACTEBEL ENERGIA")}))

    assert await succession.candidates("EGIE3", 2015) == ("EGIE3",)


async def test_two_codes_retiring_the_same_day_are_told_apart() -> None:
    # Melhoramentos' MSPA3 printed its last session on the one before EGIE3
    # opened, alongside Tractebel's — so uniqueness cannot be asked of the
    # proposals, only of what survives both witnesses.
    archive = _retired_archive()
    archive._years[2016]["MSPA3"] = _quotes(  # type: ignore[index]
        _sessions(date(2016, 7, 19), ["1.9", "2"]), name="MELHOR SP"
    )
    succession = _tape_succession(archive, frozenset({name_key("TRACTEBEL ENERGIA")}))

    assert await succession.candidates("EGIE3", 2015) == ("TBLE3", "EGIE3")
