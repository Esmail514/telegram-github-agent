"""
Unit tests for Git and Workspace timeouts.
"""
from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.runner.git import GitService
from app.runner.workspace import WorkspaceManager


@pytest.mark.asyncio
async def test_git_service_run_timeout():
    """Verify that GitService._run raises TimeoutError and kills the process on timeout."""
    git = GitService()

    # Mock asyncio.create_subprocess_exec returning a slow process
    mock_proc = MagicMock()
    mock_proc.communicate = AsyncMock(side_effect=TimeoutError())
    mock_proc.kill = MagicMock()
    mock_proc.wait = AsyncMock()

    with patch("asyncio.create_subprocess_exec", return_value=mock_proc):
        with pytest.raises(TimeoutError) as exc_info:
            await git._run("git", "status", cwd=Path("."), timeout=0.01)

        assert "git command timed out" in str(exc_info.value)
        mock_proc.kill.assert_called_once()


@pytest.mark.asyncio
async def test_workspace_clone_timeout(tmp_path: Path):
    """Verify that WorkspaceManager._git_clone raises TimeoutError and kills process on timeout."""
    manager = WorkspaceManager(workspace_root=tmp_path)

    mock_proc = MagicMock()
    mock_proc.communicate = AsyncMock(side_effect=TimeoutError())
    mock_proc.kill = MagicMock()
    mock_proc.wait = AsyncMock()

    with patch("asyncio.create_subprocess_exec", return_value=mock_proc):
        with pytest.raises(TimeoutError) as exc_info:
            await manager._git_clone("https://github.com/test/repo.git", tmp_path / "dest")

        assert "git clone timed out" in str(exc_info.value)
        mock_proc.kill.assert_called_once()
