"""Resume a local preparation at the first unfinished operation."""

import asyncio
import json
from collections.abc import Awaitable, Callable, Sequence
from contextlib import redirect_stdout
from dataclasses import dataclass
from pathlib import Path
from time import monotonic


@dataclass(frozen=True)
class PreparationStep:
    """A named CLI-composed operation and its persistent completion key."""

    key: str
    execute: Callable[[], Awaitable[int]]


async def prepare_data(
    steps: Sequence[PreparationStep],
    *,
    state_file: Path,
    revision: str,
    progress: Callable[[str], None] = print,
    operation_log: Path | None = None,
    progress_interval: float = 15,
) -> int:
    """Checkpoint successful operations; retain a failed operation for retry."""
    identity = {"revision": revision, "plan": [step.key for step in steps]}
    completed: list[str] = []
    if state_file.exists():
        try:
            saved = json.loads(state_file.read_text())
        except json.JSONDecodeError:
            saved = {}
        if isinstance(saved, dict) and all(
            saved.get(key) == value for key, value in identity.items()
        ):
            previous = saved.get("completed")
            if (
                isinstance(previous, list)
                and previous == identity["plan"][: len(previous)]
            ):
                completed = previous
    for index, step in enumerate(steps, 1):
        if step.key in completed:
            continue
        progress(f"[{index}/{len(steps)}] {step.key}")

        if operation_log is None:
            result = await _execute_step(step, progress, progress_interval)
        else:
            operation_log.parent.mkdir(parents=True, exist_ok=True)
            with operation_log.open("a") as log, redirect_stdout(log):
                result = await _execute_step(step, progress, progress_interval)
        if result:
            progress("Preparação interrompida. Execute smaug installer para retomar.")
            return result
        completed.append(step.key)
        state_file.parent.mkdir(parents=True, exist_ok=True)
        temporary = state_file.with_suffix(".tmp")
        temporary.write_text(json.dumps({**identity, "completed": completed}) + "\n")
        temporary.replace(state_file)
    return 0


async def _execute_step(
    step: PreparationStep,
    progress: Callable[[str], None],
    progress_interval: float,
) -> int:
    task = asyncio.ensure_future(step.execute())
    started = monotonic()
    try:
        while not task.done():
            done, _ = await asyncio.wait({task}, timeout=progress_interval)
            if not done:
                elapsed = int(monotonic() - started)
                progress(f"  {step.key}: em andamento ({elapsed}s)")
        return await task
    finally:
        if not task.done():
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
