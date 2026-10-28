from __future__ import annotations

from pathlib import Path

import app.reloader as reloader
from app.reloader import build_snapshot, snapshots_differ


def test_snapshot_detects_source_changes(tmp_path: Path) -> None:
    app_dir = tmp_path / "app"
    app_dir.mkdir()
    source = app_dir / "worker.py"
    source.write_text("VERSION = 1\n", encoding="utf-8")

    before = build_snapshot(tmp_path)
    source.write_text("VERSION = 200\n", encoding="utf-8")
    after = build_snapshot(tmp_path)

    assert snapshots_differ(before, after)


def test_snapshot_detects_added_and_removed_source_files(tmp_path: Path) -> None:
    app_dir = tmp_path / "app"
    app_dir.mkdir()
    before = build_snapshot(tmp_path)

    source = app_dir / "new_handler.py"
    source.write_text("enabled = True\n", encoding="utf-8")
    added = build_snapshot(tmp_path)
    source.unlink()
    removed = build_snapshot(tmp_path)

    assert snapshots_differ(before, added)
    assert snapshots_differ(added, removed)


def test_snapshot_watches_runtime_config_but_ignores_generated_files(tmp_path: Path) -> None:
    app_dir = tmp_path / "app"
    cache_dir = app_dir / "__pycache__"
    cache_dir.mkdir(parents=True)
    (app_dir / "main.py").write_text("pass\n", encoding="utf-8")
    (tmp_path / ".env").write_text("LOG_LEVEL=INFO\n", encoding="utf-8")
    (tmp_path / "pyproject.toml").write_text("[project]\n", encoding="utf-8")
    (cache_dir / "main.pyc").write_bytes(b"generated")
    (tmp_path / "notes.txt").write_text("not runtime input\n", encoding="utf-8")

    snapshot = build_snapshot(tmp_path)
    watched = {path.relative_to(tmp_path).as_posix() for path in snapshot}

    assert watched == {".env", "app/main.py", "pyproject.toml"}


def test_identical_snapshots_do_not_trigger_reload(tmp_path: Path) -> None:
    app_dir = tmp_path / "app"
    app_dir.mkdir()
    (app_dir / "main.py").write_text("pass\n", encoding="utf-8")

    snapshot = build_snapshot(tmp_path)

    assert not snapshots_differ(snapshot, build_snapshot(tmp_path))


async def test_watcher_requests_reload_after_change(monkeypatch, tmp_path: Path) -> None:
    source = tmp_path / "app" / "main.py"
    before = {source: (1, 1)}
    after = {source: (2, 1)}
    snapshots = iter((before, before, after))
    monkeypatch.setattr(reloader, "build_snapshot", lambda _root: next(snapshots))

    changed = await reloader.wait_for_source_change(
        tmp_path,
        reloader.asyncio.Event(),
        poll_interval=0.001,
        debounce_seconds=0.001,
    )

    assert changed
