"""
Tests for JobExecutor orchestration logic.
"""
import asyncio
import tempfile
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

from app.database.repository import Database, JobStatus
from app.github.client import RepoInfo
from app.github.issues import IssueInfo
from app.runner.executor import JobExecutor


@pytest.fixture
async def temp_db():
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = Path(tmpdir) / "test_exec.db"
        Database._instance = None
        db = await Database.create(db_path)
        yield db
        await db.close()
        Database._instance = None


def _mock_repo() -> RepoInfo:
    return RepoInfo(
        full_name="octocat/Hello-World",
        name="Hello-World",
        owner="octocat",
        description="Demo",
        clone_url="https://github.com/octocat/Hello-World.git",
        ssh_url="git@github.com:octocat/Hello-World.git",
        default_branch="main",
        private=False,
        html_url="https://github.com/octocat/Hello-World",
    )


def _mock_issue(num: int = 1) -> IssueInfo:
    return IssueInfo(
        number=num,
        title="Fix bug",
        body="Details",
        state="open",
        labels=["bug"],
        html_url=f"https://github.com/octocat/Hello-World/issues/{num}",
        repo_full_name="octocat/Hello-World",
    )


@pytest.mark.asyncio
async def test_prevent_multiple_active_jobs(temp_db: Database):
    executor = JobExecutor(temp_db)

    # Patch _run_job so it doesn't actually clone or call external services
    with patch.object(executor, "_run_job", new_callable=AsyncMock):
        job1 = await executor.start_job(
            repo_info=_mock_repo(),
            issue_info=_mock_issue(1),
            telegram_chat_id=123,
        )
        assert job1.job_id is not None

        # Attempting to start a second job while the first is active must raise RuntimeError
        with pytest.raises(RuntimeError, match="Another agent is already running"):
            await executor.start_job(
                repo_info=_mock_repo(),
                issue_info=_mock_issue(2),
                telegram_chat_id=123,
            )


@pytest.mark.asyncio
async def test_concurrent_start_requests_reserve_only_one_job_slot(temp_db: Database):
    executor = JobExecutor(temp_db)

    with patch.object(executor, "_run_job", new_callable=AsyncMock):
        results = await asyncio.gather(
            executor.start_job(_mock_repo(), _mock_issue(1), telegram_chat_id=123),
            executor.start_job(_mock_repo(), _mock_issue(2), telegram_chat_id=123),
            return_exceptions=True,
        )

    assert sum(isinstance(result, RuntimeError) for result in results) == 1
    assert sum(not isinstance(result, Exception) for result in results) == 1


@pytest.mark.asyncio
async def test_stop_active_job(temp_db: Database):
    executor = JobExecutor(temp_db)

    with patch.object(executor, "_run_job", new_callable=AsyncMock):
        await executor.start_job(
            repo_info=_mock_repo(),
            issue_info=_mock_issue(1),
            telegram_chat_id=123,
        )

        stopped_job = await executor.stop_active_job()
        assert stopped_job is not None
        assert stopped_job.status == JobStatus.STOPPED

        # After stopping, no job should be active
        active = await executor.get_active_job()
        assert active is None


@pytest.mark.asyncio
async def test_executor_send_notification():
    notifications = []

    async def mock_notify(msg, reply_markup=None):
        notifications.append((msg, reply_markup))

    await JobExecutor._send_notification(mock_notify, "Hello", reply_markup={"test": True})
    assert len(notifications) == 1
    assert notifications[0][0] == "Hello"
    assert notifications[0][1] == {"test": True}

    # Test single-arg notify backwards compatibility
    single_arg_notifications = []

    async def single_notify(msg):
        single_arg_notifications.append(msg)

    await JobExecutor._send_notification(single_notify, "World", reply_markup={"test": True})
    assert len(single_arg_notifications) == 1
    assert single_arg_notifications[0] == "World"


@pytest.mark.asyncio
async def test_executor_redacts_secret_like_notification_content():
    notifications = []

    async def mock_notify(msg):
        notifications.append(msg)

    await JobExecutor._send_notification(mock_notify, "token ghp_" + "A" * 36)

    assert notifications == ["token ***REDACTED***"]


@pytest.mark.asyncio
async def test_start_job_with_local_workspace_path(temp_db: Database):
    executor = JobExecutor(temp_db)
    local_path = Path("D:/MyProjects/Demo")

    with patch.object(executor, "_run_job", new_callable=AsyncMock) as mock_run:
        job = await executor.start_job(
            repo_info=_mock_repo(),
            issue_info=_mock_issue(1),
            telegram_chat_id=123,
            local_workspace_path=local_path,
        )
        assert job.job_id is not None
        mock_run.assert_called_once()
        _, kwargs = mock_run.call_args
        assert kwargs.get("local_workspace_path") == local_path


@pytest.mark.asyncio
async def test_start_local_prompt_job_uses_the_selected_agent_configuration(temp_db: Database, tmp_path: Path):
    executor = JobExecutor(temp_db)

    with patch.object(executor, "_run_job", new_callable=AsyncMock) as run_job:
        job = await executor.start_local_prompt_job(
            schedule_id="local-123abc",
            project_name="sample",
            workspace_path=tmp_path,
            prompt="افحص الاختبارات",
            telegram_chat_id=123,
            agent_name="antigravity",
            model="gpt-5.6-terra",
            reasoning_effort="high",
        )
        await asyncio.sleep(0)

    assert job.agent == "antigravity"
    assert run_job.call_args.kwargs["local_only"] is True
    assert run_job.call_args.kwargs["branch_name"] == "antigravity/scheduled-123abc"
    assert run_job.call_args.kwargs["model"] == "gpt-5.6-terra"


def test_format_completion_message_with_summary_and_files():
    from app.runner.executor import _format_completion_message

    msg = _format_completion_message(
        status_header="✅ *اكتملت المهمة بنجاح!*",
        repo_name="my-org/my-project",
        task_title="إصلاح مشكلة الجدولة",
        branch="ai/issue-42",
        summary="تم تحديث معالج الجدولة وإضافة فحوصات التحقق.",
        changed_files=["app/handler.py", "tests/test_handler.py"],
        pr_number=99,
        pr_url="https://github.com/my-org/my-project/pull/99",
    )

    assert "✅ *اكتملت المهمة بنجاح!*" in msg
    assert "my-org/my-project" in msg
    assert "إصلاح مشكلة الجدولة" in msg
    assert "ai/issue-42" in msg
    assert "تم تحديث معالج الجدولة وإضافة فحوصات التحقق." in msg
    assert "• `app/handler.py`" in msg
    assert "• `tests/test_handler.py`" in msg
    assert "[#99](https://github.com/my-org/my-project/pull/99)" in msg


def test_format_completion_message_fallback_summary_and_large_file_list():
    from app.runner.executor import _format_completion_message

    files = [f"file_{i}.py" for i in range(20)]
    msg = _format_completion_message(
        status_header="✅ *اكتملت المهمة المحلية بنجاح!*",
        repo_name="local-repo",
        task_title="Local prompt",
        branch="main",
        summary="",
        changed_files=files,
    )

    assert "تم تنفيذ التعديلات المطلوبة وتجهيز التغييرات بنجاح." in msg
    assert "الملفات المعدلة (20):" in msg
    assert "• `file_0.py`" in msg
    assert "• `file_14.py`" in msg
    assert "و 5 ملفات أخرى" in msg
