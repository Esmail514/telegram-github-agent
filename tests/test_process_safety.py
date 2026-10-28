import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.agents.antigravity import AntigravityAgent
from app.agents.base import AgentContext, AgentResult, BaseAgent
from app.agents.codex import CodexAgent
from app.agents.process_cleanup import terminate_process
from app.runner.approvals import APPROVED, STALE, ApprovalCoordinator, _compute_fingerprint
from app.runner.fallback import AgentRouter, FallbackPolicy
from app.runner.file_transfer import _is_dir_allowed


def _context():
    return AgentContext(
        repo_full_name="owner/repo", repo_url="", repo_description="", default_branch="main",
        issue_number=1, issue_title="test", issue_body="test", issue_labels=[], branch="ai/issue-1",
        repo_structure="", key_files={},
    )


class _Process:
    def __init__(self):
        self.returncode = None
        self.started = asyncio.Event()
        self.finished = asyncio.Event()
        self.stdin = MagicMock(drain=AsyncMock())
        self.stdout = SimpleNamespace(at_eof=lambda: False, readline=self._readline, read=self._read)
        self.stderr = SimpleNamespace(at_eof=lambda: False, readline=self._readline, read=self._read)
        self.terminate = MagicMock(side_effect=self._terminate)
        self.kill = MagicMock(side_effect=self._terminate)
        self.wait = AsyncMock(side_effect=self._wait)

    async def _readline(self):
        await self.finished.wait()
        return b""

    async def _read(self, size=-1):
        return await self._readline()

    async def _wait(self):
        self.started.set()
        await self.finished.wait()
        return self.returncode

    def _terminate(self):
        self.returncode = -15
        self.finished.set()


@pytest.mark.parametrize("agent_class", [CodexAgent, AntigravityAgent, "transport"])
async def test_cancelled_cli_is_terminated_reaped_and_readers_finish(
    agent_class, tmp_path, monkeypatch,
):
    process = _Process()
    monkeypatch.setattr("asyncio.create_subprocess_exec", AsyncMock(return_value=process))
    monkeypatch.setattr("shutil.which", lambda command: command)
    existing = asyncio.all_tasks()
    if agent_class == "transport":
        from app.agents.transport import AVAILABLE, CLI, CliTransport, TransportAvailability

        transport = CliTransport("codex")
        transport._executable = "codex"
        transport.probe = AsyncMock(return_value=TransportAvailability(AVAILABLE, CLI))
        coroutine = transport.run([], tmp_path)
    else:
        coroutine = agent_class().run(_context(), tmp_path, AsyncMock())
    run = asyncio.create_task(coroutine)
    try:
        await asyncio.wait_for(process.started.wait(), timeout=1)
        run.cancel()
        with pytest.raises(asyncio.CancelledError):
            await run
        process.terminate.assert_called_once()
        assert process.returncode is not None
        assert process.wait.await_count >= 2
        assert not [task for task in asyncio.all_tasks() - existing if not task.done()]
    finally:
        process._terminate()
        if not run.done():
            run.cancel()
        await asyncio.gather(run, return_exceptions=True)
        await asyncio.sleep(0)


async def test_router_stops_timed_out_agent_before_fallback(temp_db, tmp_path):
    events = []

    class Agent(BaseAgent):
        def __init__(self, name):
            self.name = name

        async def run(self, context, workspace_path, on_progress):
            if self.name == "codex":
                await asyncio.Event().wait()
            assert events == ["codex stopped"]
            return AgentResult(True, 0, "done")

        async def stop(self):
            events.append(f"{self.name} stopped")

    router = AgentRouter(temp_db, FallbackPolicy(timeout_allowed=True))
    result = await router.run(
        "job-test", "codex", _context(), tmp_path, Agent,
        timeout_secs=0.01, on_progress=AsyncMock(),
    )
    assert result.result.success
    assert events[0] == "codex stopped"


def test_allowed_directory_rejects_sibling_prefix(tmp_path, monkeypatch):
    root = tmp_path / "Allowed"
    monkeypatch.setattr(
        "app.runner.file_transfer.settings", SimpleNamespace(personal_allowed_dirs_list=[str(root)]),
    )
    assert _is_dir_allowed(root / "folder" / "report.txt")
    assert not _is_dir_allowed(tmp_path / "Allowed-Private" / "report.txt")


@pytest.mark.parametrize("missing_initial", [False, True])
async def test_approval_fails_closed_when_fingerprint_unavailable(temp_db, tmp_path, missing_initial):
    fingerprint = MagicMock(side_effect=[None if missing_initial else "initial", None])
    coordinator = ApprovalCoordinator(temp_db, fingerprint_fn=fingerprint)
    record = await coordinator.request("job-test", "before_commit", "HIGH", str(tmp_path))
    assert await coordinator.decide(record.id, APPROVED, 123, str(tmp_path)) == STALE


@pytest.mark.parametrize("source", ["tracked", "staged", "untracked"])
async def test_fingerprint_changes_when_same_file_content_changes(tmp_path, monkeypatch, source):
    state = {"content": b"original"}
    untracked = tmp_path / "new.txt"
    if source == "untracked":
        untracked.write_bytes(b"original")

    async def spawn(*args, **kwargs):
        assert args[0] == "git"
        assert kwargs.get("cwd") == str(tmp_path)
        if "diff" in args:
            selected = ("--cached" in args) == (source == "staged")
            output = state["content"] if selected and source != "untracked" else b""
        elif "ls-files" in args:
            output = b"new.txt\0" if source == "untracked" else b""
        else:
            output = b"stable-head-branch-status"
        return SimpleNamespace(returncode=0, communicate=AsyncMock(return_value=(output, b"")))

    monkeypatch.setattr("asyncio.create_subprocess_exec", spawn)
    first = await _compute_fingerprint(str(tmp_path))
    state["content"] = b"changed"
    if source == "untracked":
        untracked.write_bytes(b"changed")
    second = await _compute_fingerprint(str(tmp_path))
    assert first is not None
    assert second is not None
    assert first != second


async def test_process_that_ignores_terminate_is_killed_and_reaped():
    process = _Process()
    process.terminate.side_effect = None
    await terminate_process(process, timeout=0.01)
    process.terminate.assert_called_once()
    process.kill.assert_called_once()
    assert process.returncode is not None
    assert process.wait.await_count == 2


async def test_fingerprint_timeout_terminates_its_git_process(tmp_path, monkeypatch):
    from app.runner import approvals

    process = _Process()
    process.communicate = AsyncMock(side_effect=TimeoutError)
    monkeypatch.setattr("asyncio.create_subprocess_exec", AsyncMock(return_value=process))
    assert await approvals._compute_fingerprint(str(tmp_path)) is None
    process.terminate.assert_called_once()
    assert process.returncode is not None


async def test_reject_does_not_require_a_readable_fingerprint(temp_db):
    from app.runner.approvals import REJECTED

    coordinator = ApprovalCoordinator(temp_db, fingerprint_fn=lambda: None)
    record = await coordinator.request("job-test", "before_commit", "HIGH", None)
    assert await coordinator.decide(record.id, REJECTED, 123) == REJECTED


async def test_transport_keeps_draining_after_output_capture_limit(tmp_path, monkeypatch):
    from app.agents.transport import AVAILABLE, CLI, CliTransport, TransportAvailability

    process = _Process()
    lines = iter([b"x" * 1024 + b"\n"] * 80)

    async def readline():
        line = next(lines, b"")
        if not line:
            process.returncode = 0
            process.finished.set()
        return line

    process.stdout = SimpleNamespace(at_eof=lambda: False, readline=readline)
    monkeypatch.setattr("asyncio.create_subprocess_exec", AsyncMock(return_value=process))
    transport = CliTransport("codex")
    transport._executable = "codex"
    transport.probe = AsyncMock(return_value=TransportAvailability(AVAILABLE, CLI))

    result = await transport.run([], tmp_path, timeout=0.1)

    assert not result.timed_out
    assert result.returncode == 0
    assert "output truncated" in result.output
    assert len(result.output) < 40000


async def test_approval_decision_before_wait_is_preserved(temp_db, tmp_path):
    coordinator = ApprovalCoordinator(temp_db, fingerprint_fn=lambda: "same")
    record = await coordinator.request("job-test", "before_commit", "HIGH", str(tmp_path))
    assert coordinator.is_pending(record.id)

    assert await coordinator.decide(record.id, APPROVED, 123, str(tmp_path)) == APPROVED

    assert not coordinator.is_pending(record.id)
    assert await coordinator.wait(record.id, timeout=0.01) == APPROVED
    assert record.id not in coordinator._pending


async def test_approval_wait_timeout_cleans_up_pending_state(temp_db):
    coordinator = ApprovalCoordinator(temp_db, fingerprint_fn=lambda: "same")
    record = await coordinator.request("job-test", "before_commit", "HIGH", ".")
    assert await coordinator.wait(record.id, timeout=0.01) == "EXPIRED"
    assert record.id not in coordinator._pending


async def test_cancelled_approval_wait_cleans_up_pending_state(temp_db):
    coordinator = ApprovalCoordinator(temp_db, fingerprint_fn=lambda: "same")
    record = await coordinator.request("job-test", "before_commit", "HIGH", ".")
    waiting = asyncio.create_task(coordinator.wait(record.id))
    await asyncio.sleep(0)
    waiting.cancel()
    with pytest.raises(asyncio.CancelledError):
        await waiting
    assert record.id not in coordinator._pending
