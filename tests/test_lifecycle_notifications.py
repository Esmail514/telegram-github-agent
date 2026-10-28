import tempfile
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.database.repository import Database
from app.database.schedule_repository import ScheduledJobStatus, ScheduleRepository
from app.runner.local_projects import LocalProjectRegistry
from app.runner.local_prompt_schedules import LocalPromptScheduleStore, LocalPromptStatus
from app.runner.project_scanner import ProjectScanner
from app.runner.scheduler import JobScheduler


@pytest.fixture
async def temp_db():
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = Path(tmpdir) / "test_lifecycle.db"
        Database._instance = None
        db = await Database.create(db_path)
        yield db
        await db.close()
        Database._instance = None


@pytest.mark.asyncio
async def test_schedule_repository_pending_helpers(temp_db: Database):
    repo = ScheduleRepository(temp_db)

    now = datetime.now(UTC)
    dt1 = now + timedelta(hours=1)
    dt2 = now + timedelta(hours=2)

    assert await repo.count_pending() == 0
    assert await repo.list_all_pending() == []

    sj1 = await repo.create(
        repo_full_name="owner/repo-1",
        issue_number=10,
        agent="antigravity",
        scheduled_at=dt1,
        chat_id=123,
        project_id="project-one",
        project_name="local-one",
    )
    sj2 = await repo.create(
        repo_full_name="owner/repo-2",
        issue_number=20,
        agent="antigravity",
        scheduled_at=dt2,
        chat_id=123,
    )

    assert await repo.count_pending() == 2
    assert await repo.count_pending(chat_id=123) == 2
    assert await repo.count_pending(chat_id=999) == 0

    pending = await repo.list_all_pending()
    assert len(pending) == 2
    assert pending[0].id == sj1.id
    assert pending[0].project_id == "project-one"
    assert pending[1].id == sj2.id

    # Launch one and cancel one
    await repo.mark_launched(sj1.id)
    assert await repo.count_pending() == 1

    await repo.cancel(sj2.id)
    assert await repo.count_pending() == 0
    assert await repo.list_all_pending() == []


@pytest.mark.asyncio
async def test_scheduler_recovers_overdue_jobs(temp_db: Database):
    repo = ScheduleRepository(temp_db)
    scheduler = JobScheduler()

    # Create an overdue job (was scheduled in the past while bot was offline)
    past_time = datetime.now(UTC) - timedelta(minutes=10)
    sj = await repo.create(
        repo_full_name="owner/test-repo",
        issue_number=5,
        agent="antigravity",
        scheduled_at=past_time,
        chat_id=12345,
        project_name="test-repo",
        workspace_path="D:/Work/test-repo",
    )

    mock_bot = MagicMock()
    mock_bot.send_message = AsyncMock()

    mock_executor = MagicMock()
    mock_executor.start_job = AsyncMock()
    mock_executor.get_active_job = AsyncMock(return_value=None)

    async def check_claim_before_dispatch(**kwargs):
        claimed = await repo.get(sj.id)
        assert claimed.status == ScheduledJobStatus.LAUNCHED

    mock_executor.start_job.side_effect = check_claim_before_dispatch

    mock_repo_obj = MagicMock()
    mock_repo_obj.full_name = "owner/test-repo"
    mock_repo_obj.name = "test-repo"
    mock_repo_obj.owner.login = "owner"
    mock_repo_obj.description = "Test"
    mock_repo_obj.clone_url = "https://github.com/owner/test-repo.git"
    mock_repo_obj.ssh_url = "git@github.com:owner/test-repo.git"
    mock_repo_obj.default_branch = "main"
    mock_repo_obj.private = False
    mock_repo_obj.html_url = "https://github.com/owner/test-repo"

    mock_issue = MagicMock()
    mock_issue.number = 5
    mock_issue.title = "Past issue"

    app_context = {
        "db": temp_db,
        "executor": mock_executor,
        "bot": mock_bot,
    }

    with (
        patch("app.github.client.github_client.get_repo", return_value=mock_repo_obj),
        patch("app.github.issues.issue_service.get_issue", return_value=mock_issue),
    ):
        await scheduler._tick(app_context)

    # Verify job was marked as launched
    updated_sj = await repo.get(sj.id)
    assert updated_sj is not None
    assert updated_sj.status == ScheduledJobStatus.LAUNCHED
    assert mock_executor.start_job.call_args.kwargs["local_workspace_path"] == Path(
        "D:/Work/test-repo"
    )

    # Verify bot sent the recovery notification mentioning it was recovered
    mock_bot.send_message.assert_called()
    first_call_text = mock_bot.send_message.call_args_list[0].kwargs["text"]
    assert "استئناف مهمة مجدولة" in first_call_text or "Recovered" in first_call_text


@pytest.mark.asyncio
async def test_failed_issue_dispatch_releases_the_schedule(temp_db: Database):
    repo = ScheduleRepository(temp_db)
    job = await repo.create("owner/repo", 7, "codex", datetime.now(UTC), 123)
    executor = MagicMock()
    executor.get_active_job = AsyncMock(return_value=None)
    executor.start_job = AsyncMock(side_effect=RuntimeError("Executor is busy"))
    with (
        patch("app.github.client.github_client.get_repo", return_value=MagicMock()),
        patch("app.github.issues.issue_service.get_issue", return_value=MagicMock()),
    ):
        await JobScheduler()._tick({"db": temp_db, "executor": executor, "bot": None})
    assert (await repo.get(job.id)).status == ScheduledJobStatus.PENDING



def test_settings_lifecycle_defaults(monkeypatch):
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "123:fake_token_for_testing_only_not_real")
    monkeypatch.setenv("TELEGRAM_ALLOWED_USER_ID", "99999")
    monkeypatch.setenv("GITHUB_TOKEN", "ghp_" + "B" * 36)

    from app.config.settings import Settings
    s = Settings(_env_file=None)
    assert s.NOTIFY_ON_STARTUP is True
    assert s.NOTIFY_ON_SHUTDOWN is True


@pytest.mark.asyncio
async def test_scheduler_launches_due_csv_prompt_only_when_executor_is_free(temp_db: Database, tmp_path, monkeypatch):
    from app.config.settings import settings

    monkeypatch.setattr(settings, "TELEGRAM_ALLOWED_USER_ID", 123)

    projects_root = tmp_path / "projects"
    project_path = projects_root / "sample"
    git_dir = project_path / ".git"
    git_dir.mkdir(parents=True)
    (git_dir / "HEAD").write_text("ref: refs/heads/main\n", encoding="utf-8")
    (git_dir / "config").write_text(
        '[remote "origin"]\n\turl = https://github.com/acme/sample.git\n', encoding="utf-8"
    )
    settings.set_projects_dir(projects_root)
    registered = LocalProjectRegistry(projects_root).register(ProjectScanner().inspect_directory(project_path))
    store = LocalPromptScheduleStore(tmp_path / "local_prompts.csv")
    due = await store.create(
        project_id=registered.project_id,
        project_name="sample",
        prompt="نفذ الاختبارات",
        scheduled_at=datetime.now(UTC) - timedelta(minutes=1),
        chat_id=123,
        db=temp_db,
    )

    mock_executor = MagicMock()
    mock_executor.get_active_job = AsyncMock(return_value=None)
    mock_executor.start_local_prompt_job = AsyncMock()
    monkeypatch.setattr(LocalPromptScheduleStore, "from_settings", classmethod(lambda cls: store))

    await JobScheduler()._tick({"db": temp_db, "executor": mock_executor, "bot": None})

    assert mock_executor.start_local_prompt_job.await_count == 1
    assert mock_executor.start_local_prompt_job.call_args.kwargs["model"] == "gpt-5.6-terra"
    assert (await store.get(due.id)).status == LocalPromptStatus.LAUNCHED


@pytest.mark.asyncio
async def test_scheduler_keeps_due_csv_prompt_pending_while_another_job_runs(
    temp_db: Database, tmp_path, monkeypatch
):
    from app.config.settings import settings

    monkeypatch.setattr(settings, "TELEGRAM_ALLOWED_USER_ID", 123)

    projects_root = tmp_path / "projects"
    project_path = projects_root / "sample"
    git_dir = project_path / ".git"
    git_dir.mkdir(parents=True)
    (git_dir / "HEAD").write_text("ref: refs/heads/main\n", encoding="utf-8")
    (git_dir / "config").write_text(
        '[remote "origin"]\n\turl = https://github.com/acme/sample.git\n', encoding="utf-8"
    )
    settings.set_projects_dir(projects_root)
    registered = LocalProjectRegistry(projects_root).register(ProjectScanner().inspect_directory(project_path))
    store = LocalPromptScheduleStore(tmp_path / "local_prompts.csv")
    due = await store.create(
        project_id=registered.project_id,
        project_name="sample",
        prompt="نفذ الاختبارات",
        scheduled_at=datetime.now(UTC) - timedelta(minutes=1),
        chat_id=123,
        db=temp_db,
    )

    mock_executor = MagicMock()
    mock_executor.get_active_job = AsyncMock(return_value=MagicMock())
    mock_executor.start_local_prompt_job = AsyncMock()
    monkeypatch.setattr(LocalPromptScheduleStore, "from_settings", classmethod(lambda cls: store))

    await JobScheduler()._tick({"db": temp_db, "executor": mock_executor, "bot": None})

    assert mock_executor.start_local_prompt_job.await_count == 0
    assert (await store.get(due.id)).status == LocalPromptStatus.PENDING
