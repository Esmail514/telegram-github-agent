import asyncio
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.database.repository import JobStatus, SavedProjectRepository
from app.runner.local_prompt_schedules import LocalPromptScheduleStore, LocalPromptStatus
from app.runner.scheduler import JobScheduler


@pytest.mark.asyncio
async def test_scheduler_shutdown_awaits_tick_rollback_before_returning(temp_db, monkeypatch):
    started = asyncio.Event()
    cleanup_started = asyncio.Event()
    release = asyncio.Event()
    rolled_back = asyncio.Event()

    async def tick(context):
        started.set()
        try:
            await asyncio.Event().wait()
        finally:
            cleanup_started.set()
            await release.wait()
            await temp_db.conn.execute("SELECT 1")
            rolled_back.set()

    scheduler = JobScheduler()
    shutdown = scheduler.shutdown
    monkeypatch.setattr(scheduler, "_tick", tick)
    scheduler.start({"db": temp_db})
    await started.wait()
    shutting_down = asyncio.create_task(shutdown())
    try:
        await asyncio.wait_for(cleanup_started.wait(), timeout=1)
        assert not shutting_down.done()
    finally:
        release.set()
        await shutting_down
    assert rolled_back.is_set()
    assert scheduler._task is None or scheduler._task.done()
    await scheduler.shutdown()


@pytest.mark.asyncio
async def test_scheduler_shutdown_never_awaits_itself():
    scheduler = JobScheduler()

    async def run():
        scheduler._task = asyncio.current_task()
        await scheduler.shutdown()

    result = await asyncio.gather(asyncio.create_task(run()), return_exceptions=True)
    assert not isinstance(result[0], RuntimeError)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("owner_id", "chat_id"), [(123, 999), (999, 123), (999, 999)],
)
async def test_local_csv_owner_must_match_project_and_configured_operator(
    temp_db, tmp_path, monkeypatch, owner_id, chat_id,
):
    from app.config.settings import settings

    monkeypatch.setattr(settings, "TELEGRAM_ALLOWED_USER_ID", 123)
    project = await SavedProjectRepository(temp_db).add(owner_id, "sample", str(tmp_path), None)
    store = LocalPromptScheduleStore.for_project(tmp_path)
    saved = await store.create(f"saved:{project.id}", "sample", "prompt", datetime.now(UTC), chat_id)
    monkeypatch.setattr(LocalPromptScheduleStore, "discover", AsyncMock(return_value=[store]))
    executor = SimpleNamespace(
        get_active_job=AsyncMock(return_value=None), start_local_prompt_job=AsyncMock(),
    )
    bot = SimpleNamespace(send_message=AsyncMock())
    await JobScheduler()._tick({"db": temp_db, "executor": executor, "bot": bot})
    executor.start_local_prompt_job.assert_not_awaited()
    assert (await store.get(saved.id)).status == LocalPromptStatus.FAILED
    bot.send_message.assert_awaited_once()
    assert bot.send_message.call_args.kwargs["chat_id"] == 123


@pytest.mark.asyncio
async def test_legacy_csv_cannot_choose_foreign_notification_chat(temp_db, tmp_path, monkeypatch):
    from app.config.settings import settings

    monkeypatch.setattr(settings, "TELEGRAM_ALLOWED_USER_ID", 123)
    store = LocalPromptScheduleStore.for_project(tmp_path)
    saved = await store.create("project-legacy", "sample", "prompt", datetime.now(UTC), 999)
    monkeypatch.setattr(LocalPromptScheduleStore, "discover", AsyncMock(return_value=[store]))
    executor = SimpleNamespace(
        get_active_job=AsyncMock(return_value=None), start_local_prompt_job=AsyncMock(),
    )
    bot = SimpleNamespace(send_message=AsyncMock())
    await JobScheduler()._tick({"db": temp_db, "executor": executor, "bot": bot})
    executor.start_local_prompt_job.assert_not_awaited()
    assert (await store.get(saved.id)).status == LocalPromptStatus.FAILED
    assert bot.send_message.call_args.kwargs["chat_id"] == 123


@pytest.mark.asyncio
async def test_local_completion_during_active_shutdown_preserves_claim(temp_db, tmp_path, monkeypatch):
    from app.config.settings import settings

    monkeypatch.setattr(settings, "TELEGRAM_ALLOWED_USER_ID", 123)
    project = await SavedProjectRepository(temp_db).add(123, "sample", str(tmp_path), None)
    store = LocalPromptScheduleStore.for_project(tmp_path)
    saved = await store.create(f"saved:{project.id}", "sample", "prompt", datetime.now(UTC), 123)
    monkeypatch.setattr(LocalPromptScheduleStore, "discover", AsyncMock(return_value=[store]))
    executor = SimpleNamespace(
        get_active_job=AsyncMock(return_value=None), start_local_prompt_job=AsyncMock(),
    )
    await JobScheduler()._tick({"db": temp_db, "executor": executor})
    complete = executor.start_local_prompt_job.call_args.kwargs["completion_fn"]
    await complete(SimpleNamespace(status=JobStatus.IMPLEMENTING, error=None, current_phase=None))
    assert (await store.get(saved.id)).status == LocalPromptStatus.LAUNCHED
