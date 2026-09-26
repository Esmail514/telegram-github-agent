import tempfile
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.database.repository import Database
from app.database.schedule_repository import ScheduledJobStatus, ScheduleRepository
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
    )

    mock_bot = MagicMock()
    mock_bot.send_message = AsyncMock()

    mock_executor = MagicMock()
    mock_executor.start_job = AsyncMock()

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

    # Verify bot sent the recovery notification mentioning it was recovered
    mock_bot.send_message.assert_called()
    first_call_text = mock_bot.send_message.call_args_list[0].kwargs["text"]
    assert "استئناف مهمة مجدولة" in first_call_text or "Recovered" in first_call_text


def test_settings_lifecycle_defaults(monkeypatch):
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "123:fake_token_for_testing_only_not_real")
    monkeypatch.setenv("TELEGRAM_ALLOWED_USER_ID", "99999")
    monkeypatch.setenv("GITHUB_TOKEN", "ghp_" + "B" * 36)

    from app.config.settings import Settings
    s = Settings(_env_file=None)
    assert s.NOTIFY_ON_STARTUP is True
    assert s.NOTIFY_ON_SHUTDOWN is True
