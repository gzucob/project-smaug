"""Manage the local CLI worker independently of read API requests."""

import asyncio
import logging
import sys

logger = logging.getLogger(__name__)


async def supervise_price_worker(interval_seconds: int) -> None:
    """Run immediately, then periodically; cancel and reap the CLI on shutdown."""
    while True:
        process: asyncio.subprocess.Process | None = None
        try:
            process = await asyncio.create_subprocess_exec(
                sys.executable, "-m", "smaug.entrypoints.cli", "sync-prices"
            )
            result = await process.wait()
            if result:
                logger.warning("Price CLI exited with status %d; will retry", result)
        except asyncio.CancelledError:
            if process is not None and process.returncode is None:
                process.terminate()
                try:
                    await asyncio.wait_for(process.wait(), timeout=10)
                except TimeoutError:
                    process.kill()
                    await process.wait()
            raise
        except Exception:
            logger.exception("Could not run automatic price CLI")
        await asyncio.sleep(interval_seconds)
