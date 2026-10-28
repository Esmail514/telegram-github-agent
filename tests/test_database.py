"""
Tests for database operations and repository methods.
"""
import tempfile
from pathlib import Path

import pytest

from app.database.repository import (
    Database,
    JobRepository,
    JobStatus,
    ProjectRootRepository,
    SelectedProjectRepository,
)
from app.database.schedule_repository import ScheduledJobStatus, ScheduleRepository


@pytest.fixture
async def temp_db():
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = Path(tmpdir) / "test_agent.db"
        # Reset singleton for isolation
        Database._instance = None
        db = await Database.create(db_path)
        yield db
        await db.close()
        Database._instance = None


@pytest.mark.asyncio
async def test_create_and_get_job(temp_db: Database):
    repo = JobRepository(temp_db)
    job = await repo.create_job(
        job_id="test-job-1",
        repo_full_name="octocat/Hello-World",
        issue_number=42,
        branch="agent/issue-42",
        agent="opencode",
        telegram_chat_id=12345,
    )

    assert job.job_id == "test-job-1"
    assert job.repo_full_name == "octocat/Hello-World"
    assert job.issue_number == 42
    assert job.branch == "agent/issue-42"
    assert job.status == JobStatus.QUEUED
    assert job.telegram_chat_id == 12345

    fetched = await repo.get_job("test-job-1")
    assert fetched is not None
    assert fetched.job_id == "test-job-1"


@pytest.mark.asyncio
async def test_get_active_job(temp_db: Database):
    repo = JobRepository(temp_db)
    assert await repo.get_active_job() is None

    await repo.create_job(
        job_id="active-job-1",
        repo_full_name="octocat/Hello-World",
        issue_number=1,
        branch="agent/issue-1",
        agent="opencode",
        telegram_chat_id=123,
    )
    active = await repo.get_active_job()
    assert active is not None
    assert active.job_id == "active-job-1"

    # Mark as completed
    await repo.set_status("active-job-1", JobStatus.COMPLETED)
    assert await repo.get_active_job() is None


@pytest.mark.asyncio
async def test_set_status_terminal_sets_finished_at(temp_db: Database):
    repo = JobRepository(temp_db)
    await repo.create_job(
        job_id="job-terminal",
        repo_full_name="octocat/Hello-World",
        issue_number=10,
        branch="agent/issue-10",
        agent="opencode",
        telegram_chat_id=123,
    )

    job = await repo.get_job("job-terminal")
    assert job.finished_at is None

    updated = await repo.set_status("job-terminal", JobStatus.COMPLETED, phase="Done")
    assert updated.status == JobStatus.COMPLETED
    assert updated.current_phase == "Done"
    assert updated.finished_at is not None


@pytest.mark.asyncio
async def test_update_job_fields(temp_db: Database):
    repo = JobRepository(temp_db)
    await repo.create_job(
        job_id="job-update",
        repo_full_name="octocat/Hello-World",
        issue_number=5,
        branch="agent/issue-5",
        agent="opencode",
        telegram_chat_id=123,
    )

    updated = await repo.update_job(
        "job-update",
        pr_url="https://github.com/octocat/Hello-World/pull/6",
        pr_number=6,
        files_changed=3,
    )
    assert updated.pr_url == "https://github.com/octocat/Hello-World/pull/6"
    assert updated.pr_number == 6
    assert updated.files_changed == 3


@pytest.mark.asyncio
async def test_list_recent_jobs(temp_db: Database):
    repo = JobRepository(temp_db)
    for i in range(5):
        await repo.create_job(
            job_id=f"job-{i}",
            repo_full_name=f"octocat/repo-{i}",
            issue_number=i,
            branch=f"agent/issue-{i}",
            agent="opencode",
            telegram_chat_id=123,
        )

    recent = await repo.list_recent_jobs(limit=3)
    assert len(recent) == 3


@pytest.mark.asyncio
async def test_selected_project_is_persisted_per_user(temp_db: Database):
    repo = SelectedProjectRepository(temp_db)

    await repo.save(123, "telegram-bot", "D:/Work/telegram-bot", "owner/telegram-bot")

    selected = await repo.get(123)
    assert selected is not None
    assert selected.name == "telegram-bot"
    assert selected.path == "D:/Work/telegram-bot"
    assert selected.repo_full_name == "owner/telegram-bot"


@pytest.mark.asyncio
async def test_selected_project_can_be_changed(temp_db: Database):
    repo = SelectedProjectRepository(temp_db)
    await repo.save(123, "first", "D:/Work/first", None)
    await repo.save(123, "second", "D:/Work/second", "owner/second")

    selected = await repo.get(123)
    assert selected is not None
    assert selected.name == "second"


@pytest.mark.asyncio
async def test_selected_project_paths_can_be_cleared_without_deleting_projects(temp_db: Database):
    repo = SelectedProjectRepository(temp_db)
    await repo.save(123, "first", "D:/Work/first", "owner/first")
    await repo.save(456, "second", "E:/Work/second", "owner/second")

    assert await repo.clear_paths() == 2

    first = await repo.get(123)
    second = await repo.get(456)
    assert first is not None and second is not None
    assert (first.name, first.path, first.repo_full_name) == ("first", "", "owner/first")
    assert (second.name, second.path, second.repo_full_name) == ("second", "", "owner/second")


@pytest.mark.asyncio
async def test_confirmed_projects_root_is_persisted_separately(temp_db: Database):
    roots = ProjectRootRepository(temp_db)

    await roots.save(123, "D:/Projects")
    await roots.save(123, "E:/Development")

    root = await roots.get(123)
    assert root is not None
    assert root.path == "E:/Development"


@pytest.mark.asyncio
async def test_pending_scheduled_issue_can_be_rescheduled_or_started_now(temp_db: Database):
    from datetime import UTC, datetime, timedelta

    schedules = ScheduleRepository(temp_db)
    created = await schedules.create(
        repo_full_name="owner/repo",
        issue_number=7,
        agent="codex",
        scheduled_at=datetime.now(UTC) + timedelta(hours=1),
        chat_id=123,
    )

    assert await schedules.update_time(created.id, datetime.now(UTC))
    updated = await schedules.get(created.id)
    assert updated is not None and updated.status == ScheduledJobStatus.PENDING
@pytest.mark.asyncio
async def test_scheduled_issue_claim_is_atomic_and_normalizes_timezone(temp_db: Database):
    from datetime import UTC, datetime, timedelta, timezone

    repo = ScheduleRepository(temp_db)
    local_time = datetime(2026, 10, 7, 15, 0, tzinfo=timezone(timedelta(hours=3)))
    job = await repo.create("owner/repo", 1, "codex", local_time, 123)

    assert job.scheduled_dt == datetime(2026, 10, 7, 12, 0, tzinfo=UTC)
    assert await repo.mark_launched(job.id) is True
    assert await repo.mark_launched(job.id) is False
    assert await repo.cancel(job.id) is False
