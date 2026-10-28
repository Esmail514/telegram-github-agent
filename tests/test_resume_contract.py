import asyncio
from datetime import UTC, datetime
from unittest.mock import AsyncMock

import pytest

from app.database.repository import JobRepository, JobStateRepository, JobStatus
from app.runner.executor import JobExecutor
from app.runner.local_prompt_schedules import LocalPromptScheduleStore, LocalPromptStatus
from app.runner.recovery import token_limit_error


async def _paused_local_job(temp_db, tmp_path, monkeypatch):
    from app.config.settings import settings

    monkeypatch.setattr(settings, "WORKSPACE_DIR", tmp_path / "workspaces")
    store = LocalPromptScheduleStore.for_project(tmp_path)
    schedule = await store.create(
        project_id="saved:1", project_name="sample", prompt="Original prompt\nwith detail",
        scheduled_at=datetime.now(UTC), chat_id=123, agent="codex", model="selected-model",
        reasoning_effort="low",
    )
    await store.mark_launched(schedule.id)
    executor = JobExecutor(temp_db)
    run = AsyncMock()
    monkeypatch.setattr(executor, "_run_job", run)
    job = await executor.start_local_prompt_job(
        schedule_id=schedule.id, project_name="sample", workspace_path=tmp_path,
        prompt=schedule.prompt, telegram_chat_id=123, agent_name="codex",
        model="selected-model", reasoning_effort="low",
    )
    await executor._active_task
    await JobRepository(temp_db).update_job(
        job.job_id, status=JobStatus.FAILED, workspace_path=str(tmp_path),
        error=token_limit_error(),
    )
    run.reset_mock()
    return executor, store, schedule, job, run


@pytest.mark.asyncio
async def test_resume_preserves_csv_prompt_execution_and_completion(temp_db, tmp_path, monkeypatch):
    executor, store, schedule, job, run = await _paused_local_job(temp_db, tmp_path, monkeypatch)
    state = await JobStateRepository(temp_db).get_state(job.job_id)
    assert state.metadata["issue"]["body"] == ""

    await executor.resume_job(job.job_id)
    await executor._active_task

    args, kwargs = run.call_args
    assert args[2].body == schedule.prompt
    assert kwargs["local_only"] is True
    assert kwargs["model"] == "selected-model"
    assert kwargs["reasoning_effort"] == "low"
    assert kwargs["branch_name"] == job.branch
    await JobRepository(temp_db).set_status(job.job_id, JobStatus.COMPLETED)
    await kwargs["completion_fn"](await JobRepository(temp_db).get_job(job.job_id))
    assert (await store.get(schedule.id)).status == LocalPromptStatus.COMPLETED


@pytest.mark.asyncio
async def test_resume_refuses_changed_csv_prompt(temp_db, tmp_path, monkeypatch):
    executor, store, schedule, job, run = await _paused_local_job(temp_db, tmp_path, monkeypatch)
    rows = store._read_rows()
    rows[0]["prompt"] = "Different task"
    store._write_rows(rows)

    with pytest.raises(ValueError, match="changed|modified"):
        await executor.resume_job(job.job_id)
    run.assert_not_called()
    assert (await JobRepository(temp_db).get_job(job.job_id)).status == JobStatus.FAILED


@pytest.mark.asyncio
async def test_concurrent_resumes_reserve_one_slot(temp_db, tmp_path, monkeypatch):
    executor, store, schedule, job, run = await _paused_local_job(temp_db, tmp_path, monkeypatch)
    results = await asyncio.gather(
        executor.resume_job(job.job_id), executor.resume_job(job.job_id),
        return_exceptions=True,
    )
    await executor._active_task
    assert sum(not isinstance(result, Exception) for result in results) == 1
    assert run.call_count == 1


@pytest.mark.asyncio
async def test_stop_waits_for_running_task_cleanup(temp_db, tmp_path, monkeypatch):
    executor, store, schedule, job, run = await _paused_local_job(temp_db, tmp_path, monkeypatch)
    started = asyncio.Event()
    cleaned = asyncio.Event()
    cleanup_started = asyncio.Event()
    release_cleanup = asyncio.Event()

    async def running(*args, **kwargs):
        started.set()
        try:
            await asyncio.Event().wait()
        finally:
            cleanup_started.set()
            await release_cleanup.wait()
            cleaned.set()

    monkeypatch.setattr(executor, "_run_job", running)
    await executor.resume_job(job.job_id)
    await started.wait()
    stopping = asyncio.create_task(executor.stop_active_job())
    try:
        await asyncio.wait_for(cleanup_started.wait(), timeout=1)
        finished, _ = await asyncio.wait({stopping}, timeout=0.02)
        assert not finished
    finally:
        release_cleanup.set()
        await stopping
        await asyncio.gather(executor._active_task, return_exceptions=True)
    assert cleaned.is_set()
    assert executor._active_task.done()


@pytest.mark.asyncio
async def test_explicit_stop_completes_local_csv_bookkeeping(temp_db, tmp_path, monkeypatch):
    executor, store, schedule, job, run = await _paused_local_job(temp_db, tmp_path, monkeypatch)
    started = asyncio.Event()

    async def running(*args, **kwargs):
        started.set()
        try:
            await asyncio.Event().wait()
        finally:
            current = await JobRepository(temp_db).get_job(job.job_id)
            await kwargs["completion_fn"](current)

    monkeypatch.setattr(executor, "_run_job", running)
    await executor.resume_job(job.job_id)
    await started.wait()
    await executor.stop_active_job()
    assert (await JobRepository(temp_db).get_job(job.job_id)).status == JobStatus.STOPPED
    assert (await store.get(schedule.id)).status == LocalPromptStatus.FAILED


@pytest.mark.asyncio
async def test_executor_busy_includes_terminal_job_completion(temp_db, tmp_path, monkeypatch):
    executor, store, schedule, job, run = await _paused_local_job(temp_db, tmp_path, monkeypatch)
    entered = asyncio.Event()
    release = asyncio.Event()
    async def finishing(*args, **kwargs):
        await JobRepository(temp_db).set_status(job.job_id, JobStatus.COMPLETED)
        entered.set()
        await release.wait()
    monkeypatch.setattr(executor, "_run_job", finishing)
    await executor.resume_job(job.job_id)
    await entered.wait()
    try:
        assert await executor.is_busy()
    finally:
        release.set()
        await executor._active_task
    assert not await executor.is_busy()


@pytest.mark.asyncio
async def test_shutdown_preserves_resumable_local_job(temp_db, tmp_path, monkeypatch):
    from app.runner.recovery import is_interrupted

    executor, store, schedule, job, run = await _paused_local_job(temp_db, tmp_path, monkeypatch)
    entered = asyncio.Event()
    async def running(*args, **kwargs):
        entered.set()
        await asyncio.Event().wait()
    monkeypatch.setattr(executor, "_run_job", running)
    await executor.resume_job(job.job_id)
    await entered.wait()

    await executor.shutdown()

    assert executor._active_task.done()
    assert is_interrupted(await JobRepository(temp_db).get_job(job.job_id))
    assert (await store.get(schedule.id)).status == LocalPromptStatus.LAUNCHED
    with pytest.raises(RuntimeError, match="shutting down"):
        await executor.resume_job(job.job_id)
