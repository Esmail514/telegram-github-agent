from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import aiosqlite
import pytest

from app.database.repository import Database, SavedProjectRepository
from app.database.schedule_repository import ScheduleRepository
from app.runner.local_prompt_schedules import LocalPromptScheduleStore, LocalPromptStatus
from app.runner.scheduler import JobScheduler


@pytest.fixture(autouse=True)
def configured_test_operator(default_env, monkeypatch):
    from app.config.settings import settings

    monkeypatch.setattr(settings, "TELEGRAM_ALLOWED_USER_ID", 123)


@pytest.mark.asyncio
async def test_issue_execution_options_survive_save_and_dispatch(temp_db, monkeypatch):
    repo = ScheduleRepository(temp_db)
    scheduled = await repo.create(
        "owner/sample", 13, "codex", datetime.now(UTC), 123,
        model="chosen-model", reasoning_effort="low",
    )
    loaded = await repo.get(scheduled.id)
    assert loaded.model == "chosen-model"
    assert loaded.reasoning_effort == "low"
    executor = SimpleNamespace(get_active_job=AsyncMock(return_value=None), start_job=AsyncMock())
    monkeypatch.setattr(LocalPromptScheduleStore, "discover", AsyncMock(return_value=[]))
    with (
        patch("app.github.client.github_client.get_repo", return_value=MagicMock()),
        patch("app.github.issues.issue_service.get_issue", return_value=MagicMock()),
    ):
        await JobScheduler()._tick({"db": temp_db, "executor": executor})
    assert executor.start_job.call_args.kwargs["model"] == "chosen-model"
    assert executor.start_job.call_args.kwargs["reasoning_effort"] == "low"


@pytest.mark.asyncio
async def test_issue_confirmation_persists_execution_options(temp_db):
    from app.telegram.handlers.schedule import sched_confirm_callback

    context = SimpleNamespace(
        bot_data={"db": temp_db},
        user_data={
            "sched_mode": "issue", "sched_repo": "owner/sample", "sched_issue": 13,
            "sched_dt": (datetime.now(UTC) + timedelta(hours=1)).isoformat(),
            "sched_agent": "codex", "sched_model": "chosen-model", "sched_effort": "xhigh",
        },
    )
    update = SimpleNamespace(
        effective_user=SimpleNamespace(id=123), effective_chat=SimpleNamespace(id=123),
        callback_query=SimpleNamespace(answer=AsyncMock(), edit_message_text=AsyncMock()),
    )
    await sched_confirm_callback(update, context)
    saved = (await ScheduleRepository(temp_db).list_all_pending())[0]
    assert saved.model == "chosen-model"
    assert saved.reasoning_effort == "xhigh"


@pytest.mark.asyncio
async def test_issue_options_migrate_legacy_database_without_losing_rows(tmp_path):
    path = tmp_path / "legacy.db"
    async with aiosqlite.connect(path) as conn:
        await conn.execute(
            "CREATE TABLE scheduled_jobs (id INTEGER PRIMARY KEY, repo_full_name TEXT, "
            "issue_number INTEGER, agent TEXT, scheduled_at TEXT, chat_id INTEGER, "
            "project_id TEXT, project_name TEXT, workspace_path TEXT, status TEXT, created_at TEXT)"
        )
        await conn.execute(
            "INSERT INTO scheduled_jobs VALUES "
            "(1, 'owner/sample', 13, 'codex', '2026-01-01T00:00:00Z', 123, "
            "NULL, NULL, NULL, 'PENDING', '2026-01-01T00:00:00Z')"
        )
        await conn.commit()
    Database._instance = None
    db = await Database.create(path)
    try:
        await Database._apply_migrations(db.conn)
        saved = await ScheduleRepository(db).get(1)
        assert saved.repo_full_name == "owner/sample"
        assert saved.model is None
        assert saved.reasoning_effort is None
    finally:
        await db.close()
        Database._instance = None


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("field", "bad_value"),
    [("project_id", "saved:abc"), ("project_id", "saved:9223372036854775808"),
     ("scheduled_at", "not-a-date"),
     ("chat_id", "not-a-chat"), ("agent", "unsupported")],
)
async def test_bad_csv_row_is_failed_without_blocking_valid_task(
    temp_db, tmp_path, monkeypatch, field, bad_value,
):
    saved_project = await SavedProjectRepository(temp_db).add(123, "sample", str(tmp_path), None)
    store = LocalPromptScheduleStore.for_project(tmp_path)
    bad = await store.create(
        f"saved:{saved_project.id}", "sample", "bad prompt",
        datetime.now(UTC) - timedelta(minutes=2), 123,
    )
    good = await store.create(
        f"saved:{saved_project.id}", "sample", "valid prompt",
        datetime.now(UTC) - timedelta(minutes=1), 123,
    )
    rows = store._read_rows()
    rows[0][field] = bad_value
    store._write_rows(rows)
    monkeypatch.setattr(LocalPromptScheduleStore, "discover", AsyncMock(return_value=[store]))
    executor = SimpleNamespace(
        get_active_job=AsyncMock(return_value=None), start_local_prompt_job=AsyncMock(),
    )
    bot = SimpleNamespace(send_message=AsyncMock())
    scheduler = JobScheduler()
    await scheduler._tick({"db": temp_db, "executor": executor, "bot": bot})
    await scheduler._tick({"db": temp_db, "executor": executor, "bot": bot})

    executor.start_local_prompt_job.assert_awaited_once()
    assert executor.start_local_prompt_job.call_args.kwargs["schedule_id"] == good.id
    assert executor.start_local_prompt_job.call_args.kwargs["schedule_store_path"] == store.path
    rows = {row["id"]: row for row in store._read_rows()}
    assert rows[bad.id]["status"] == LocalPromptStatus.FAILED
    assert rows[bad.id][field] == bad_value
    assert rows[bad.id]["last_error"]
    assert rows[good.id]["status"] == LocalPromptStatus.LAUNCHED
    bot.send_message.assert_awaited_once()


@pytest.mark.asyncio
async def test_invalid_csv_encoding_is_safe_and_reported_once(temp_db, tmp_path, monkeypatch):
    store = LocalPromptScheduleStore.for_project(tmp_path)
    store.path.write_bytes(b"\xff\xfeinvalid-csv")
    monkeypatch.setattr(LocalPromptScheduleStore, "discover", AsyncMock(return_value=[store]))
    bot = SimpleNamespace(send_message=AsyncMock())
    executor = SimpleNamespace(get_active_job=AsyncMock(return_value=None))
    scheduler = JobScheduler()
    for _ in range(2):
        await scheduler._tick({"db": temp_db, "executor": executor, "bot": bot})
    bot.send_message.assert_awaited_once()
    assert store.path.read_bytes() == b"\xff\xfeinvalid-csv"


@pytest.mark.asyncio
async def test_resume_csv_status_is_explicit_and_does_not_requeue_cancelled_task(tmp_path):
    store = LocalPromptScheduleStore.for_project(tmp_path)
    saved = await store.create("saved:1", "sample", "prompt", datetime.now(UTC), 123)
    assert not await store.mark_resumed(saved.id)
    await store.mark_failed(saved.id, "Interrupted legacy task")
    assert await store.mark_resumed(saved.id)
    assert (await store.get(saved.id)).status == LocalPromptStatus.LAUNCHED
    assert await store.mark_resumed(saved.id)
    assert await store.mark_completed(saved.id)
    assert not await store.mark_resumed(saved.id)
    cancelled = await store.create("saved:1", "sample", "cancelled", datetime.now(UTC), 123)
    await store.cancel(cancelled.id)
    assert not await store.mark_resumed(cancelled.id)


@pytest.mark.asyncio
async def test_duplicate_csv_ids_fail_without_discarding_other_rows(tmp_path):
    store = LocalPromptScheduleStore.for_project(tmp_path)
    duplicate = await store.create("saved:1", "sample", "duplicate", datetime.now(UTC), 123)
    good = await store.create("saved:1", "sample", "valid", datetime.now(UTC), 123)
    rows = store._read_rows()
    rows.insert(1, dict(rows[0]))
    store._write_rows(rows)

    due, errors = await store.list_due_with_errors(datetime.now(UTC))
    assert [item.id for item in due] == [good.id]
    assert len(errors) == 2
    assert [row["id"] for row in store._read_rows()].count(duplicate.id) == 2
    assert len(await store.list_all(ignore_invalid=True)) == 1
    assert await store.get(good.id) is not None
    assert await store.get(duplicate.id) is None


@pytest.mark.asyncio
async def test_blank_csv_status_is_normalized_before_claim(tmp_path):
    store = LocalPromptScheduleStore.for_project(tmp_path)
    saved = await store.create("saved:1", "sample", "valid", datetime.now(UTC), 123)
    rows = store._read_rows()
    rows[0]["status"] = ""
    store._write_rows(rows)

    due, errors = await store.list_due_with_errors(datetime.now(UTC))
    assert not errors
    assert [item.id for item in due] == [saved.id]
    assert await store.mark_launched(saved.id)


@pytest.mark.asyncio
async def test_local_token_pause_keeps_schedule_resumable(temp_db, tmp_path, monkeypatch):
    from app.database.repository import JobStatus
    from app.runner.recovery import TOKEN_LIMIT_MARKER

    project = await SavedProjectRepository(temp_db).add(123, "sample", str(tmp_path), None)
    store = LocalPromptScheduleStore.for_project(tmp_path)
    saved = await store.create(
        f"saved:{project.id}", "sample", "valid", datetime.now(UTC), 123,
    )
    monkeypatch.setattr(LocalPromptScheduleStore, "discover", AsyncMock(return_value=[store]))
    executor = SimpleNamespace(
        get_active_job=AsyncMock(return_value=None), start_local_prompt_job=AsyncMock(),
    )
    await JobScheduler()._tick({"db": temp_db, "executor": executor})
    completion = executor.start_local_prompt_job.call_args.kwargs["completion_fn"]
    await completion(SimpleNamespace(status=JobStatus.FAILED, error=TOKEN_LIMIT_MARKER))
    assert (await store.get(saved.id)).status == LocalPromptStatus.LAUNCHED
    await completion(SimpleNamespace(status=JobStatus.COMPLETED))
    assert (await store.get(saved.id)).status == LocalPromptStatus.COMPLETED


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "message", ["Another agent is already running.", "The previous agent is still finishing cleanup."],
)
async def test_local_dispatch_releases_busy_claim_and_runs_on_next_tick(
    temp_db, tmp_path, monkeypatch, message,
):
    from app.runner.executor import ExecutorBusyError

    project = await SavedProjectRepository(temp_db).add(123, "sample", str(tmp_path), None)
    store = LocalPromptScheduleStore.for_project(tmp_path)
    saved = await store.create(f"saved:{project.id}", "sample", "prompt", datetime.now(UTC), 123)
    monkeypatch.setattr(LocalPromptScheduleStore, "discover", AsyncMock(return_value=[store]))
    executor = SimpleNamespace(
        get_active_job=AsyncMock(return_value=None),
        start_local_prompt_job=AsyncMock(side_effect=[ExecutorBusyError(message), None]),
    )
    bot = SimpleNamespace(send_message=AsyncMock())
    scheduler = JobScheduler()
    await scheduler._tick({"db": temp_db, "executor": executor, "bot": bot})
    assert (await store.get(saved.id)).status == LocalPromptStatus.PENDING
    bot.send_message.assert_not_awaited()

    await scheduler._tick({"db": temp_db, "executor": executor, "bot": bot})
    assert (await store.get(saved.id)).status == LocalPromptStatus.LAUNCHED
    assert executor.start_local_prompt_job.await_count == 2


@pytest.mark.asyncio
async def test_actual_local_dispatch_failure_stays_visible(temp_db, tmp_path, monkeypatch):
    project = await SavedProjectRepository(temp_db).add(123, "sample", str(tmp_path), None)
    store = LocalPromptScheduleStore.for_project(tmp_path)
    saved = await store.create(f"saved:{project.id}", "sample", "prompt", datetime.now(UTC), 123)
    monkeypatch.setattr(LocalPromptScheduleStore, "discover", AsyncMock(return_value=[store]))
    executor = SimpleNamespace(
        get_active_job=AsyncMock(return_value=None),
        start_local_prompt_job=AsyncMock(side_effect=RuntimeError("Workspace cannot be opened")),
    )
    bot = SimpleNamespace(send_message=AsyncMock())
    await JobScheduler()._tick({"db": temp_db, "executor": executor, "bot": bot})
    failed = await store.get(saved.id)
    assert failed.status == LocalPromptStatus.FAILED
    assert failed.last_error == "Workspace cannot be opened"
    bot.send_message.assert_awaited_once()


@pytest.mark.asyncio
async def test_missing_saved_project_is_failed_not_requeued(temp_db, tmp_path, monkeypatch):
    project = await SavedProjectRepository(temp_db).add(
        123, "sample", str(tmp_path / "missing-project"), None,
    )
    store = LocalPromptScheduleStore.for_project(tmp_path)
    saved = await store.create(f"saved:{project.id}", "sample", "prompt", datetime.now(UTC), 123)
    monkeypatch.setattr(LocalPromptScheduleStore, "discover", AsyncMock(return_value=[store]))
    executor = SimpleNamespace(
        get_active_job=AsyncMock(return_value=None), start_local_prompt_job=AsyncMock(),
    )
    bot = SimpleNamespace(send_message=AsyncMock())
    await JobScheduler()._tick({"db": temp_db, "executor": executor, "bot": bot})
    assert (await store.get(saved.id)).status == LocalPromptStatus.FAILED
    executor.start_local_prompt_job.assert_not_awaited()
    bot.send_message.assert_awaited_once()


@pytest.mark.asyncio
async def test_csv_release_launch_preserves_terminal_statuses(tmp_path):
    store = LocalPromptScheduleStore.for_project(tmp_path)
    saved = await store.create("saved:1", "sample", "prompt", datetime.now(UTC), 123)
    assert not await store.release_launch(saved.id)
    assert await store.mark_launched(saved.id)
    assert await store.release_launch(saved.id)
    assert (await store.get(saved.id)).status == LocalPromptStatus.PENDING
    assert await store.cancel(saved.id)
    assert not await store.release_launch(saved.id)
    assert (await store.get(saved.id)).status == LocalPromptStatus.CANCELLED


@pytest.mark.asyncio
async def test_bad_csv_store_does_not_block_a_different_project(temp_db, tmp_path, monkeypatch):
    invalid = LocalPromptScheduleStore(tmp_path / "invalid.csv")
    invalid.path.write_bytes(b"invalid columns\n")
    project_path = tmp_path / "valid-project"
    project_path.mkdir()
    project = await SavedProjectRepository(temp_db).add(123, "sample", str(project_path), None)
    valid = LocalPromptScheduleStore.for_project(project_path)
    saved = await valid.create(f"saved:{project.id}", "sample", "prompt", datetime.now(UTC), 123)
    monkeypatch.setattr(LocalPromptScheduleStore, "discover", AsyncMock(return_value=[invalid, valid]))
    executor = SimpleNamespace(
        get_active_job=AsyncMock(return_value=None), start_local_prompt_job=AsyncMock(),
    )
    bot = SimpleNamespace(send_message=AsyncMock())
    await JobScheduler()._tick({"db": temp_db, "executor": executor, "bot": bot})
    executor.start_local_prompt_job.assert_awaited_once()
    assert executor.start_local_prompt_job.call_args.kwargs["schedule_id"] == saved.id
    bot.send_message.assert_awaited_once()
    assert invalid.path.read_bytes() == b"invalid columns\n"
