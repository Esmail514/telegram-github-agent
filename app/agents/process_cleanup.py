"""Bounded cleanup for agent subprocesses and their output readers."""
from __future__ import annotations

import asyncio
from collections.abc import Iterable


async def terminate_process(process: asyncio.subprocess.Process | None, timeout: float = 5.0) -> None:
    if process is None or process.returncode is not None:
        return
    stdin = getattr(process, "stdin", None)
    if stdin is not None:
        stdin.close()
    try:
        process.terminate()
    except ProcessLookupError:
        pass
    try:
        await asyncio.wait_for(process.wait(), timeout=timeout)
    except TimeoutError:
        try:
            process.kill()
        except ProcessLookupError:
            pass
        await asyncio.wait_for(process.wait(), timeout=timeout)


async def finish_process(
    process: asyncio.subprocess.Process | None,
    readers: Iterable[asyncio.Task],
) -> None:
    try:
        await terminate_process(process)
    finally:
        tasks = list(readers)
        for task in tasks:
            if not task.done():
                task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
