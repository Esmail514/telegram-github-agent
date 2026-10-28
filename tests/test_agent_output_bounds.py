import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.agents.antigravity import AntigravityAgent
from app.agents.base import AgentContext
from app.agents.codex import CodexAgent


def _context():
    return AgentContext(
        repo_full_name="owner/repo", repo_url="", repo_description="", default_branch="main",
        issue_number=1, issue_title="test", issue_body="test", issue_labels=[], branch="ai/issue-1",
        repo_structure="", key_files={},
    )


def _stream(data):
    stream = asyncio.StreamReader()
    stream.feed_data(data)
    stream.feed_eof()
    return stream


@pytest.mark.parametrize("agent_class", [CodexAgent, AntigravityAgent])
async def test_cli_drains_large_unbroken_output_and_retains_bounded_tail(
    agent_class, tmp_path, monkeypatch,
):
    stdout = _stream(b"x" * (512 * 1024) + b"\ncompleted at end\n")
    stderr = _stream(b"y" * (256 * 1024))
    process = SimpleNamespace(
        stdin=MagicMock(drain=AsyncMock()), stdout=stdout, stderr=stderr,
        wait=AsyncMock(return_value=0), returncode=0,
    )
    monkeypatch.setattr("asyncio.create_subprocess_exec", AsyncMock(return_value=process))
    monkeypatch.setattr("shutil.which", lambda command: command)

    result = await agent_class().run(_context(), tmp_path, AsyncMock())

    assert result.success, result.error
    assert result.raw_output.endswith("completed at end")
    assert len(result.raw_output.encode("utf-8")) <= 128 * 1024
    assert await stdout.read() == b""
    assert await stderr.read() == b""


@pytest.mark.parametrize("agent_class", [CodexAgent, AntigravityAgent])
async def test_cli_remembers_quota_signal_even_after_output_tail_rotates(
    agent_class, tmp_path, monkeypatch,
):
    process = SimpleNamespace(
        stdin=MagicMock(drain=AsyncMock()), stdout=_stream(b"done\n"),
        stderr=_stream(b"quota exceeded\n" + b"x" * (256 * 1024)),
        wait=AsyncMock(return_value=1), returncode=1,
    )
    monkeypatch.setattr("asyncio.create_subprocess_exec", AsyncMock(return_value=process))
    monkeypatch.setattr("shutil.which", lambda command: command)

    result = await agent_class().run(_context(), tmp_path, AsyncMock())

    assert not result.success
    assert result.token_exhausted
    assert len((result.error or "").encode("utf-8")) <= 128 * 1024


async def test_quota_signal_split_across_read_chunks_is_detected():
    from app.agents.output_capture import READ_CHUNK_BYTES, OutputCapture

    capture = OutputCapture()
    await capture.drain(_stream(b"x" * (READ_CHUNK_BYTES - 6) + b"quota exceeded"))

    assert capture.token_exhausted
