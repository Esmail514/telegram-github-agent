from __future__ import annotations

import asyncio
import logging
from pathlib import Path

logger = logging.getLogger(__name__)

FileSignature = tuple[int, int]
Snapshot = dict[Path, FileSignature]
_ROOT_FILES = (".env", "pyproject.toml", "requirements.txt")


def build_snapshot(project_root: Path) -> Snapshot:
    root = project_root.resolve()
    candidates = [root / name for name in _ROOT_FILES]
    app_dir = root / "app"
    if app_dir.is_dir():
        candidates.extend(app_dir.rglob("*.py"))

    snapshot: Snapshot = {}
    for path in candidates:
        try:
            stat = path.stat()
        except (FileNotFoundError, OSError):
            continue
        if path.is_file():
            snapshot[path] = (stat.st_mtime_ns, stat.st_size)
    return snapshot


def snapshots_differ(previous: Snapshot, current: Snapshot) -> bool:
    return previous != current


async def wait_for_source_change(
    project_root: Path,
    stop_event: asyncio.Event,
    *,
    poll_interval: float = 1.0,
    debounce_seconds: float = 0.35,
) -> bool:
    snapshot = build_snapshot(project_root)
    while not stop_event.is_set():
        try:
            await asyncio.wait_for(stop_event.wait(), timeout=poll_interval)
            return False
        except TimeoutError:
            current = build_snapshot(project_root)

        if not snapshots_differ(snapshot, current):
            continue

        logger.info("Source change detected; waiting for writes to settle")
        try:
            await asyncio.wait_for(stop_event.wait(), timeout=debounce_seconds)
            return False
        except TimeoutError:
            return True
    return False
