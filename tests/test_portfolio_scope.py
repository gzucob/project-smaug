"""CLI scope regressions for the complete-universe default."""

import pytest
import typer

from smaug.entrypoints import cli
from smaug.ingestion.domain.runs import TickerScope


def test_no_filter_selects_the_complete_universe() -> None:
    assert cli._resolve_scope(None, False) == ((), True)
    assert cli._resolve_scope([], False) == ((), True)
    assert cli._resolve_scope(None, True) == ((), True)


def test_explicit_tickers_remain_a_narrow_scope() -> None:
    assert cli._resolve_scope(["PETR4", "VALE3"], False) == (
        ("PETR4", "VALE3"),
        False,
    )


def test_all_and_ticker_cannot_be_combined() -> None:
    with pytest.raises(typer.BadParameter, match="mutually exclusive"):
        cli._resolve_scope(["PETR4"], True)


def test_commands_without_a_filter_dispatch_to_the_complete_universe(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: dict[str, tuple[tuple[str, ...], dict[str, object]]] = {}

    def fake_run(name: str):
        async def run(tickers: tuple[str, ...], **kwargs: object) -> int:
            calls[name] = (tickers, kwargs)
            return 0

        return run

    monkeypatch.setattr(cli, "_run_ingest", fake_run("ingest"))
    monkeypatch.setattr(cli, "_run_report", fake_run("report"))
    monkeypatch.setattr(cli, "_run_analyze", fake_run("analyze"))
    monkeypatch.setattr(cli, "_run_doctor", fake_run("doctor"))

    with pytest.raises(typer.Exit):
        cli.ingest(
            ticker=None,
            all_listed=False,
            document=None,
            year=None,
            from_year=None,
            to_year=None,
            force=False,
            verbose=False,
            concurrency=8,
        )
    with pytest.raises(typer.BadParameter, match="provide --ticker or --all"):
        cli.report(ticker=None, all_listed=False)
    cli.report(ticker=None, all_listed=True)
    with pytest.raises(typer.Exit):
        cli.analyze(ticker=None, all_listed=False, verbose=False)
    with pytest.raises(typer.Exit):
        cli.doctor(ticker=None, all_listed=False, verbose=False)

    tickers, kwargs = calls["report"]
    assert tickers == ()
    assert kwargs["whole_exchange"] is True
    for name in ("analyze", "doctor"):
        tickers, kwargs = calls[name]
        assert tickers == ()
        assert kwargs["whole_exchange"] is True
    tickers, kwargs = calls["ingest"]
    assert tickers == ()
    assert kwargs["whole_exchange"] is True
    assert kwargs["ticker_scope"] is TickerScope.ALL
