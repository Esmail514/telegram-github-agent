import asyncio
from unittest.mock import AsyncMock, Mock

import pytest

from app.runner.git import GitService
from app.runner.workspace import WorkspaceManager


@pytest.mark.asyncio
@pytest.mark.parametrize("operation", ["command", "clone", "pull"])
async def test_cancelled_git_operation_reaps_process(tmp_path, monkeypatch, operation):
    started = asyncio.Event()

    async def communicate():
        started.set()
        await asyncio.Event().wait()

    process = Mock(returncode=None, communicate=communicate, wait=AsyncMock())
    monkeypatch.setattr(asyncio, "create_subprocess_exec", AsyncMock(return_value=process))
    manager = WorkspaceManager(tmp_path)
    if operation == "command":
        pending = GitService()._run("git", "status", cwd=tmp_path)
    elif operation == "clone":
        pending = manager._git_clone("https://example.com/owner/repo.git", tmp_path / "repo")
    else:
        pending = manager._git_pull(tmp_path)
    task = asyncio.create_task(pending)
    await started.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert process.terminate.called or process.kill.called
    process.wait.assert_awaited()
