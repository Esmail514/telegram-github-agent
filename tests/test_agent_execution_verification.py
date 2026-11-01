"""
Comprehensive 10-point verification test suite for AI agents and project improvements.

Verifies:
 1. Antigravity CLI command construction & auto-approval flags.
 2. Antigravity Agent execution, output capture & summary extraction.
 3. Codex CLI command construction & full-auto flags.
 4. Codex Agent execution & fallback mechanisms.
 5. Dynamic agent routing fidelity in JobExecutor (antigravity vs codex).
 6. Local project improvement detection, git commit, and local completion.
 7. No-changes guard (rejection of runs that made no file modifications).
 8. Secret scanner protection blocking commits with sensitive credentials.
 9. Scheduled tasks agent fidelity & completed task filtering in UI.
10. Personal mode task runner routing with selected agent and clean termination.
"""
from __future__ import annotations

import asyncio
import tempfile
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.agents.antigravity import AntigravityAgent, build_antigravity_cli_command
from app.agents.base import AgentContext, AgentResult
from app.agents.codex import CodexAgent, build_codex_cli_command
from app.database.repository import Database, JobRepository, JobStatus
from app.database.schedule_repository import ScheduledJob, ScheduleRepository
from app.github.client import RepoInfo
from app.github.issues import IssueInfo
from app.runner.executor import JobExecutor
from app.runner.personal_task import PersonalTaskManager
from app.runner.scheduler import JobScheduler


@pytest.fixture
async def test_db():
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = Path(tmpdir) / "test_verification.db"
        Database._instance = None
        db = await Database.create(db_path)
        yield db
        await db.close()
        Database._instance = None


def _dummy_context(repo_name: str = "demo/proj", issue_num: int = 1, model: str | None = None, effort: str | None = None) -> AgentContext:
    return AgentContext(
        repo_full_name=repo_name,
        repo_url=f"https://github.com/{repo_name}.git",
        repo_description="Test project",
        default_branch="main",
        issue_number=issue_num,
        issue_title="Implement feature",
        issue_body="Please add feature X to the project",
        issue_labels=["enhancement"],
        branch="ai/issue-1",
        repo_structure="main.py\nREADME.md",
        key_files={"README.md": "# Demo"},
        model=model,
        reasoning_effort=effort,
    )


# ---------------------------------------------------------------------------
# Check 1: Antigravity CLI Command & Auto-Approval
# ---------------------------------------------------------------------------
def test_check_1_antigravity_command_auto_approval_and_models():
    cmd = build_antigravity_cli_command(
        executable="agy",
        prompt="modify code",
        model="gemini-2.5-pro",
        reasoning_effort="high",
    )
    assert cmd[0] == "agy"
    assert "--dangerously-skip-permissions" in cmd
    assert "--model" in cmd
    assert cmd[cmd.index("--model") + 1] == "gemini-2.5-pro"
    assert "--effort" in cmd
    assert cmd[cmd.index("--effort") + 1] == "high"
    assert "--prompt" in cmd
    assert cmd[-1] == "modify code"

    # Verify large prompt protection (>28k chars) points to task file
    large_prompt = "x" * 30000
    large_cmd = build_antigravity_cli_command("agy", large_prompt)
    assert ".antigravity_task.md" in large_cmd[-1]
    assert len(large_cmd[-1]) < 500


# ---------------------------------------------------------------------------
# Check 2: Antigravity Agent Execution & Output Summary
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_check_2_antigravity_agent_execution_and_summary(tmp_path: Path):
    agent = AntigravityAgent()
    context = _dummy_context()
    progress_msgs = []

    async def mock_progress(msg: str):
        progress_msgs.append(msg)

    mock_proc = MagicMock()
    mock_proc.returncode = 0
    mock_proc.wait = AsyncMock(return_value=0)
    mock_proc.stdout = asyncio.StreamReader()
    mock_proc.stdout.feed_data(b"I have inspected the project and updated main.py with feature X.\nAll tests passed.\n")
    mock_proc.stdout.feed_eof()
    mock_proc.stderr = asyncio.StreamReader()
    mock_proc.stderr.feed_eof()

    with patch("asyncio.create_subprocess_exec", return_value=mock_proc):
        result = await agent.run(context, tmp_path, mock_progress)

    assert result.success is True
    assert result.exit_code == 0
    assert "updated main.py with feature X" in result.summary
    assert any("Launching Antigravity" in m for m in progress_msgs)


# ---------------------------------------------------------------------------
# Check 3: Codex CLI Command Construction
# ---------------------------------------------------------------------------
def test_check_3_codex_cli_command_construction():
    cmd = build_codex_cli_command(
        executable="codex",
        model="gpt-5.6-terra",
        reasoning_effort="high",
    )
    assert cmd == [
        "codex",
        "exec",
        "--json",
        "--full-auto",
        "--model",
        "gpt-5.6-terra",
        "-c",
        'model_reasoning_effort="high"',
        "-",
    ]


# ---------------------------------------------------------------------------
# Check 4: Codex Agent Execution & Fallback
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_check_4_codex_agent_execution_and_unavailable(tmp_path: Path):
    agent = CodexAgent()
    context = _dummy_context()

    # When CLI is not on PATH and OPENAI_API_KEY is not set
    with patch("shutil.which", return_value=None), patch("app.config.settings.settings.OPENAI_API_KEY", None):
        result = await agent.run(context, tmp_path, AsyncMock())

    assert result.success is False
    assert "Codex not available" in result.summary


# ---------------------------------------------------------------------------
# Check 5: Dynamic Agent Routing Fidelity in JobExecutor
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_check_5_job_executor_routes_to_chosen_agent(test_db: Database, tmp_path: Path):
    executor = JobExecutor(test_db)
    repo_info = RepoInfo("demo/repo", "repo", "demo", "", "", "", "main", False, "")
    issue_info = IssueInfo(1, "Fix", "body", "open", [], "", "demo/repo")

    with patch.object(executor, "_run_job", new_callable=AsyncMock) as mock_run:
        job_anti = await executor.start_job(repo_info, issue_info, 123, agent_name="antigravity")
        assert job_anti.agent == "antigravity"
        assert mock_run.call_args[0][3] == "antigravity"

    with patch.object(executor, "_run_job", new_callable=AsyncMock) as mock_run_codex:
        await test_db._conn.execute("DELETE FROM jobs")
        await test_db._conn.commit()
        job_codex = await executor.start_job(repo_info, issue_info, 123, agent_name="codex")
        assert job_codex.agent == "codex"
        assert mock_run_codex.call_args[0][3] == "codex"


# ---------------------------------------------------------------------------
# Check 6: Local Project Improvement Detection & Git Commit
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_check_6_local_project_improvement_commits_and_completes(test_db: Database, tmp_path: Path):
    executor = JobExecutor(test_db)
    notifications = []

    async def mock_notify(msg, reply_markup=None):
        notifications.append(msg)

    # Pre-create the job in the database
    job_repo = JobRepository(test_db)
    await job_repo.create_job(
        job_id="test-job-local",
        repo_full_name="local/sample",
        issue_number=0,
        branch="antigravity/scheduled-1",
        agent="antigravity",
        telegram_chat_id=123,
    )

    mock_status = MagicMock()
    mock_status.has_changes = True
    mock_status.changed_files = ["app/main.py"]
    mock_status.untracked = ["utils.py"]

    mock_agent_result = AgentResult(
        success=True,
        exit_code=0,
        summary="Refactored directory navigation and added error boundaries.",
        files_changed=["app/main.py", "utils.py"],
    )

    with (
        patch("app.runner.executor.git_service.status", AsyncMock(return_value=mock_status)),
        patch("app.runner.executor.git_service.configure_identity", AsyncMock()),
        patch("app.runner.executor.git_service.create_branch", AsyncMock()),
        patch("app.runner.executor.git_service.commit", AsyncMock()) as mock_commit,
        patch("app.runner.fallback.AgentRouter.run") as mock_router_run,
        patch("app.runner.approvals.ApprovalPolicy.gate_required", return_value=False),
        patch("app.runner.verification.VerificationPipeline.run", AsyncMock(return_value=MagicMock(blocking=False, summary=lambda *args: "all passed"))),
    ):
        mock_outcome = MagicMock()
        mock_outcome.result = mock_agent_result
        mock_router_run.return_value = mock_outcome

        await executor._run_job(
            job_id="test-job-local",
            repo_info=RepoInfo("local/sample", "sample", "local", "", "", "", "local", True, ""),
            issue_info=IssueInfo(0, "Local improvement", "details", "open", ["local-prompt"], "", "local/sample"),
            agent_name="antigravity",
            notify_fn=mock_notify,
            local_workspace_path=tmp_path,
            local_only=True,
            branch_name="antigravity/scheduled-1",
        )

    mock_commit.assert_called_once()
    active = await executor.get_active_job()
    assert active is None  # Finished successfully
    job = await job_repo.get_job("test-job-local")
    assert job is not None
    assert job.status == JobStatus.COMPLETED
    assert job.files_changed == 2
    assert any("اكتملت المهمة المحلية بنجاح" in n for n in notifications)
    assert any("Refactored directory navigation" in n for n in notifications)


# ---------------------------------------------------------------------------
# Check 7: No-Changes Guard Rejects Empty Improvements
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_check_7_no_changes_guard_fails_when_agent_makes_no_edits(test_db: Database, tmp_path: Path):
    executor = JobExecutor(test_db)
    notifications = []

    async def mock_notify(msg, reply_markup=None):
        notifications.append(msg)

    job_repo = JobRepository(test_db)
    await job_repo.create_job(
        job_id="test-job-nochange",
        repo_full_name="local/sample",
        issue_number=0,
        branch="antigravity/scheduled-2",
        agent="antigravity",
        telegram_chat_id=123,
    )

    mock_status = MagicMock()
    mock_status.has_changes = False
    mock_status.changed_files = []
    mock_status.untracked = []

    with (
        patch("app.runner.executor.git_service.status", AsyncMock(return_value=mock_status)),
        patch("app.runner.executor.git_service.configure_identity", AsyncMock()),
        patch("app.runner.executor.git_service.create_branch", AsyncMock()),
        patch("app.runner.fallback.AgentRouter.run") as mock_router_run,
    ):
        mock_outcome = MagicMock()
        mock_outcome.result = AgentResult(success=True, exit_code=0, summary="Nothing needed.")
        mock_router_run.return_value = mock_outcome

        await executor._run_job(
            job_id="test-job-nochange",
            repo_info=RepoInfo("local/sample", "sample", "local", "", "", "", "local", True, ""),
            issue_info=IssueInfo(0, "Improve", "body", "open", ["local-prompt"], "", "local/sample"),
            agent_name="antigravity",
            notify_fn=mock_notify,
            local_workspace_path=tmp_path,
            local_only=True,
        )

    job = await job_repo.get_job("test-job-nochange")
    assert job is not None
    assert job.status == JobStatus.FAILED
    assert "no file changes" in (job.error or "")
    assert any("made no file changes" in n for n in notifications)


# ---------------------------------------------------------------------------
# Check 8: Secret Scanner Protection
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_check_8_secret_scanner_blocks_leaked_credentials(test_db: Database, tmp_path: Path):
    from app.runner.git import SecretDetectedError
    executor = JobExecutor(test_db)
    notifications = []

    async def mock_notify(msg, reply_markup=None):
        notifications.append(msg)

    job_repo = JobRepository(test_db)
    await job_repo.create_job(
        job_id="test-job-secrets",
        repo_full_name="local/sample",
        issue_number=0,
        branch="antigravity/scheduled-3",
        agent="antigravity",
        telegram_chat_id=123,
    )

    mock_status = MagicMock()
    mock_status.has_changes = True
    mock_status.changed_files = [".env"]
    mock_status.untracked = []

    with (
        patch("app.runner.executor.git_service.status", AsyncMock(return_value=mock_status)),
        patch("app.runner.executor.git_service.configure_identity", AsyncMock()),
        patch("app.runner.executor.git_service.create_branch", AsyncMock()),
        patch("app.runner.executor.git_service.commit", side_effect=SecretDetectedError("Secret detected")),
        patch("app.runner.fallback.AgentRouter.run") as mock_router_run,
        patch("app.runner.approvals.ApprovalPolicy.gate_required", return_value=False),
        patch("app.runner.verification.VerificationPipeline.run", AsyncMock(return_value=MagicMock(blocking=False, summary=lambda *args: "all passed"))),
    ):
        mock_outcome = MagicMock()
        mock_outcome.result = AgentResult(success=True, exit_code=0, summary="Added config.")
        mock_router_run.return_value = mock_outcome

        await executor._run_job(
            job_id="test-job-secrets",
            repo_info=RepoInfo("local/sample", "sample", "local", "", "", "", "local", True, ""),
            issue_info=IssueInfo(0, "Config", "body", "open", ["local-prompt"], "", "local/sample"),
            agent_name="antigravity",
            notify_fn=mock_notify,
            local_workspace_path=tmp_path,
            local_only=True,
        )

    job = await job_repo.get_job("test-job-secrets")
    assert job is not None
    assert job.status == JobStatus.FAILED
    assert "Secret detected" in (job.error or "")
    assert any("potential secret detected" in n for n in notifications)


# ---------------------------------------------------------------------------
# Check 9: Scheduled Tasks Agent Fidelity & Completed Filtering
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_check_9_scheduler_agent_fidelity_and_filtering(test_db: Database, tmp_path: Path):
    from app.telegram.handlers.schedule import _show_all_schedules

    # 1. Scheduler dispatches with exact agent
    scheduler = JobScheduler()
    executor = MagicMock()
    executor.get_active_job = AsyncMock(return_value=None)
    executor.start_job = AsyncMock()

    mock_due_job = ScheduledJob(
        id=10,
        repo_full_name="demo/proj",
        issue_number=5,
        scheduled_at="2026-11-01T00:00:00Z",
        chat_id=123,
        agent="codex",
        project_id=None,
        project_name=None,
        workspace_path=None,
        status="PENDING",
        created_at="2026-11-01T00:00:00Z",
    )
    with (
        patch.object(ScheduleRepository, "list_pending", AsyncMock(return_value=[mock_due_job])),
        patch.object(ScheduleRepository, "mark_launched", AsyncMock(return_value=True)),
        patch("app.github.client.github_client.get_repo", return_value=MagicMock()),
        patch("app.github.issues.issue_service.get_issue", return_value=IssueInfo(5, "Task", "body", "open", [], "", "demo/proj")),
        patch("app.github.client.RepoInfo.from_github", return_value=RepoInfo("demo/proj", "proj", "demo", "", "", "", "main", False, "")),
        patch("app.runner.local_prompt_schedules.LocalPromptScheduleStore.discover", AsyncMock(return_value=[])),
    ):
        await scheduler._dispatch_due({"db": test_db, "executor": executor, "bot": MagicMock()})
        executor.start_job.assert_called_once()
        assert executor.start_job.call_args.kwargs["agent_name"] == "codex"

    # 2. UI hides completed tasks by default
    update = MagicMock()
    update.effective_chat.id = 123
    context = MagicMock()
    context.bot_data = {"db": test_db}

    # Add one completed job and one pending job to schedule repo
    schedule_repo = ScheduleRepository(test_db)
    sched_time = datetime.now(UTC) + timedelta(hours=2)
    await schedule_repo.create(
        repo_full_name="demo/repo",
        issue_number=1,
        agent="antigravity",
        scheduled_at=sched_time,
        chat_id=123,
    )
    await schedule_repo.create(
        repo_full_name="demo/repo",
        issue_number=2,
        agent="codex",
        scheduled_at=sched_time + timedelta(hours=1),
        chat_id=123,
    )
    # Mark job #1 completed in database
    await test_db._conn.execute("UPDATE scheduled_jobs SET status = 'COMPLETED' WHERE issue_number = 1")
    await test_db._conn.commit()

    with (
        patch("app.telegram.handlers.schedule._local_schedules_for_chat", AsyncMock(return_value=[])),
        patch("app.telegram.handlers.schedule._show") as mock_show,
    ):
        await _show_all_schedules(update, context, show_completed=False)
        _, markup = mock_show.call_args[0][1], mock_show.call_args[0][2]
        # Only the pending job #2 is shown
        buttons = [btn.text for row in markup.inline_keyboard for btn in row]
        assert any("#2" in b for b in buttons)
        assert not any("#1" in b for b in buttons)
        assert any("عرض المهام المكتملة" in b for b in buttons)


# ---------------------------------------------------------------------------
# Check 10: Personal Mode Task Execution with Chosen Agent
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_check_10_personal_task_runner_with_chosen_agent(tmp_path: Path):
    manager = PersonalTaskManager()

    mock_agent = MagicMock()
    mock_agent.name = "codex"
    mock_agent.run = AsyncMock(return_value=AgentResult(
        success=True,
        exit_code=0,
        summary="Optimized files and verified output.",
        files_changed=["file1.txt"],
    ))

    with patch("app.agents.manager.agent_manager.get_agent", return_value=mock_agent) as mock_get_agent:
        result = await manager.run_task(
            prompt="Analyze logs",
            workspace_path=tmp_path,
            agent_name="codex",
            on_progress=AsyncMock(),
        )

    mock_get_agent.assert_called_once_with("codex")
    assert result.success is True
    assert "Optimized files" in result.summary
    assert manager.is_busy() is False
