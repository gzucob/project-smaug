"""Manage the local CLI worker independently of read API requests."""

import asyncio
import logging
import sys

logger = logging.getLogger(__name__)


async def supervise_price_worker(interval_seconds: int) -> None:
    """Run the background CLI periodically and reap it on shutdown."""
    # Give the local application a head start on the first health and page reads.
    await asyncio.sleep(10)
    while True:
        process: asyncio.subprocess.Process | None = None
        try:
            process = await asyncio.create_subprocess_exec(
                sys.executable, "-m", "smaug.entrypoints.cli", "sync-updates"
            )
            result = await process.wait()
            if result:
                logger.warning("Update CLI exited with status %d; will retry", result)
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
            logger.exception("Could not run automatic update CLI")
        await asyncio.sleep(interval_seconds)
