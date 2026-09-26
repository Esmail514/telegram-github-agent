"""
Tests for token limit detection, pause, and resumption across Antigravity and JobExecutor.
"""
from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.agents.antigravity import AntigravityAgent
from app.agents.base import AgentContext, AgentResult
from app.database.repository import Database, JobRepository, JobStateRepository, JobStatus
from app.runner.executor import JobExecutor
from app.runner.fallback import FailureCode, classify_failure
from app.runner.recovery import is_interrupted, is_resumable, is_token_limit, token_limit_error


def make_dummy_context() -> AgentContext:
    return AgentContext(
        repo_full_name="owner/test-repo",
        repo_url="https://github.com/owner/test-repo.git",
        repo_description="Test repo",
        default_branch="main",
        issue_number=42,
        issue_title="Fix token issue",
        issue_body="Please implement this feature.",
        issue_labels=["bug"],
        branch="ai/issue-42",
        repo_structure="src/\n  main.py",
        key_files={},
    )


def test_classify_failure_token_exhausted() -> None:
    res = AgentResult(
        success=False,
        exit_code=-2,
        summary="Quota exceeded",
        error="Resource exhausted: 429",
        token_exhausted=True,
    )
    assert classify_failure(res) == FailureCode.RATE_LIMIT


def test_classify_failure_token_text() -> None:
    res = AgentResult(
        success=False,
        exit_code=1,
        summary="Failed to run prompt",
        error="Your account quota exceeded. Please upgrade.",
    )
    assert classify_failure(res) == FailureCode.RATE_LIMIT


@pytest.mark.asyncio
async def test_recovery_helpers(temp_db: Database) -> None:
    job_repo = JobRepository(temp_db)
    await job_repo.create_job(
        job_id="test-123",
        repo_full_name="owner/repo",
        issue_number=1,
        branch="ai/issue-1",
        agent="antigravity",
        telegram_chat_id=12345,
    )
    await job_repo.update_job(
        "test-123",
        status=JobStatus.FAILED,
        error=token_limit_error("work_account"),
    )
    updated = await job_repo.get_job("test-123")
    assert updated is not None
    assert is_token_limit(updated) is True
    assert is_interrupted(updated) is False
    assert is_resumable(updated) is True


@pytest.mark.asyncio
async def test_antigravity_continuation_prompt(tmp_path: Path) -> None:
    agent = AntigravityAgent()
    context = make_dummy_context()

    # Create existing .antigravity_task.md simulating previous run
    task_file = tmp_path / ".antigravity_task.md"
    task_file.write_text("Previous prompt content", encoding="utf-8")

    mock_process = AsyncMock()
    mock_process.returncode = 0
    mock_process.stdout = AsyncMock()
    mock_process.stdout.at_eof = MagicMock(side_effect=[False, True])
    mock_process.stdout.readline = AsyncMock(return_value=b"Completed successfully\n")
    mock_process.stderr = AsyncMock()
    mock_process.stderr.at_eof = MagicMock(return_value=True)

    with patch("asyncio.create_subprocess_exec", return_value=mock_process) as mock_exec:
        result = await agent.run(context, tmp_path, AsyncMock())
        assert result.success is True
        # Verify that prompt passed to CLI execution contained continuation instructions
        cmd_args = mock_exec.call_args[0]
        prompt_arg = cmd_args[2]  # ["agy", "--prompt", <prompt>]
        assert "RESUMED TASK CONTINUATION" in prompt_arg
        assert "git status" in prompt_arg


@pytest.mark.asyncio
async def test_executor_resume_token_limited_job(temp_db: Database, tmp_path: Path) -> None:
    job_repo = JobRepository(temp_db)
    state_repo = JobStateRepository(temp_db)

    job_id = "test-job-42"
    ws_dir = tmp_path / "workspace"
    ws_dir.mkdir()

    await job_repo.create_job(
        job_id=job_id,
        repo_full_name="owner/test-repo",
        issue_number=42,
        branch="ai/issue-42",
        agent="antigravity",
        telegram_chat_id=111,
    )
    await job_repo.update_job(
        job_id,
        status=JobStatus.FAILED,
        workspace_path=str(ws_dir),
        error=token_limit_error("account1"),
    )

    # Persist bundle metadata
    metadata = {
        "repo": {
            "full_name": "owner/test-repo",
            "name": "test-repo",
            "owner": "owner",
            "description": "desc",
            "clone_url": "https://example.com/repo.git",
            "ssh_url": "",
            "default_branch": "main",
            "private": False,
            "html_url": "https://example.com",
        },
        "issue": {
            "number": 42,
            "title": "Title",
            "body": "Body",
            "state": "open",
            "labels": [],
            "html_url": "https://example.com/issue/42",
            "repo_full_name": "owner/test-repo",
        },
    }
    await state_repo.set_state(job_id, stage="implementing", metadata=metadata)

    executor = JobExecutor(temp_db)
    with patch.object(executor, "_run_job", new_callable=AsyncMock):
        resumed = await executor.resume_job(job_id)
        assert resumed is not None
        assert resumed.job_id == job_id
        # Single active task should be launched
        assert executor._active_task is not None
        # Cancel active task before exiting to avoid dangling tasks
        executor._active_task.cancel()
