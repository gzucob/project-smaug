"""The price worker retries failures and reaps its child on shutdown."""

import asyncio
import sys
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, call

import pytest

from smaug.shared import price_worker


async def test_worker_retries_failed_process_launch_and_nonzero_exit(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    child = SimpleNamespace(wait=AsyncMock(return_value=17))
    spawn = AsyncMock(side_effect=[OSError("launch failed"), child])
    sleep = AsyncMock(side_effect=[None, None, asyncio.CancelledError])
    monkeypatch.setattr(
        price_worker,
        "asyncio",
        SimpleNamespace(
            CancelledError=asyncio.CancelledError,
            create_subprocess_exec=spawn,
            sleep=sleep,
        ),
    )

    with pytest.raises(asyncio.CancelledError):
        await price_worker.supervise_price_worker(30)

    assert sleep.await_args_list == [call(10), call(30), call(30)]
    assert (
        spawn.await_args_list
        == [call(sys.executable, "-m", "smaug.entrypoints.cli", "sync-updates")] * 2
    )
    assert "Could not run automatic update CLI" in caplog.text
    assert "exited with status 17" in caplog.text


@pytest.mark.parametrize("ignores_terminate", [False, True])
async def test_worker_terminates_and_reaps_the_cli_on_cancel(
    ignores_terminate: bool, monkeypatch: pytest.MonkeyPatch
) -> None:
    response = TimeoutError if ignores_terminate else 0
    child = SimpleNamespace(
        returncode=None,
        wait=AsyncMock(side_effect=[asyncio.CancelledError, response, 0]),
        terminate=Mock(),
        kill=Mock(),
    )
    monkeypatch.setattr(
        price_worker,
        "asyncio",
        SimpleNamespace(
            CancelledError=asyncio.CancelledError,
            create_subprocess_exec=AsyncMock(return_value=child),
            sleep=AsyncMock(),
            wait_for=asyncio.wait_for,
        ),
    )

    with pytest.raises(asyncio.CancelledError):
        await price_worker.supervise_price_worker(30)

    child.terminate.assert_called_once_with()
    assert child.kill.call_count == int(ignores_terminate)
    assert child.wait.await_count == (3 if ignores_terminate else 2)
