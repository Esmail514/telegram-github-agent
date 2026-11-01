"""
Tests for live terminal viewer and OutputCapture chunk streaming.
"""
from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from app.agents.output_capture import OutputCapture
from app.runner.terminal_viewer import TerminalViewer


def test_output_capture_listener_receives_streamed_chunks():
    capture = OutputCapture()
    received_chunks: list[bytes] = []

    def on_chunk(chunk: bytes) -> None:
        received_chunks.append(chunk)

    capture.add_listener(on_chunk)
    capture.append(b"Hello ")
    capture.append(b"World\n")

    assert b"".join(received_chunks) == b"Hello World\n"
    assert capture.get_text() == "Hello World"

    capture.remove_listener(on_chunk)
    capture.append(b"Extra text")
    assert len(received_chunks) == 2


@pytest.mark.asyncio
async def test_terminal_viewer_disabled(tmp_path: Path):
    viewer = TerminalViewer(
        workspace_path=tmp_path,
        title="Disabled Viewer",
        auto_close_seconds=10,
        enabled=False,
    )
    await viewer.start()
    viewer.write(b"Should not write\n")
    await viewer.finish(success=True)

    assert not viewer.log_file.exists()
    assert not viewer.done_file.exists()


@pytest.mark.asyncio
async def test_terminal_viewer_lifecycle(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    # Enable viewer in test environment without launching real OS window
    monkeypatch.setenv("HEADLESS_TERMINAL_TEST", "0")
    monkeypatch.delenv("PYTEST_CURRENT_TEST", raising=False)
    monkeypatch.setattr("platform.system", lambda: "Windows")
    monkeypatch.setattr("subprocess.run", lambda *args, **kwargs: None)

    viewer = TerminalViewer(
        workspace_path=tmp_path,
        title="Test Terminal Viewer",
        auto_close_seconds=5,
        enabled=True,
    )
    assert viewer.auto_close_seconds == 5

    await viewer.start()
    assert viewer.log_file.exists()
    assert viewer.script_file.exists()

    viewer.write(b"Line 1: agent started\n")
    viewer.write(b"Line 2: file modified\n")

    await viewer.finish(success=True, summary="Changes applied successfully")

    assert viewer.done_file.exists()
    done_content = viewer.done_file.read_text(encoding="utf-8")
    assert "SUCCESS" in done_content
    assert "Changes applied successfully" in done_content

    log_content = viewer.log_file.read_text(encoding="utf-8")
    assert "Line 1: agent started" in log_content
    assert "Line 2: file modified" in log_content


@pytest.mark.asyncio
async def test_output_capture_drain_streams_to_viewer(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("HEADLESS_TERMINAL_TEST", "0")
    monkeypatch.delenv("PYTEST_CURRENT_TEST", raising=False)
    monkeypatch.setattr("platform.system", lambda: "Windows")
    monkeypatch.setattr("subprocess.run", lambda *args, **kwargs: None)

    viewer = TerminalViewer(workspace_path=tmp_path, title="Drain Test", enabled=True)
    await viewer.start()

    capture = OutputCapture()
    capture.add_listener(viewer.write)

    reader = asyncio.StreamReader()
    reader.feed_data(b"Installing packages...\nBuild complete.\n")
    reader.feed_eof()

    await capture.drain(reader)
    await viewer.finish(success=True)

    log_content = viewer.log_file.read_text(encoding="utf-8")
    assert "Installing packages..." in log_content
    assert "Build complete." in log_content
