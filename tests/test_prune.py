"""prune keep-selection: keep the newest run per (ticker, view, reference_date)."""

from datetime import UTC, date, datetime

from smaug.analysis.infrastructure.sql_repository import _latest_ids, _RunKey


def _run(
    row_id: int,
    *,
    ticker: str = "PETR4",
    view: str = "closed_year",
    ref: date = date(2024, 12, 31),
    at: datetime = datetime(2026, 7, 1, tzinfo=UTC),
) -> _RunKey:
    return _RunKey(
        id=row_id, ticker=ticker, view=view, reference_date=ref, computed_at=at
    )


def test_keeps_only_the_newest_run_of_a_cell() -> None:
    stale = _run(1, at=datetime(2026, 6, 1, tzinfo=UTC))
    latest = _run(2, at=datetime(2026, 7, 1, tzinfo=UTC))

    assert _latest_ids([stale, latest]) == {2}


def test_each_cell_keeps_its_own_latest() -> None:
    # Two distinct cells (different reference_date and view) each survive.
    a_old = _run(1, ref=date(2023, 12, 31), at=datetime(2026, 6, 1, tzinfo=UTC))
    a_new = _run(2, ref=date(2023, 12, 31), at=datetime(2026, 7, 1, tzinfo=UTC))
    b = _run(3, view="ttm_live", ref=date(2026, 3, 31))
    c = _run(4, ticker="VALE3")

    assert _latest_ids([a_old, a_new, b, c]) == {2, 3, 4}


def test_ties_on_computed_at_break_by_id() -> None:
    same_instant = datetime(2026, 7, 1, tzinfo=UTC)
    lo = _run(1, at=same_instant)
    hi = _run(2, at=same_instant)

    assert _latest_ids([lo, hi]) == {2}


def test_empty_table_keeps_nothing() -> None:
    assert _latest_ids([]) == set()
