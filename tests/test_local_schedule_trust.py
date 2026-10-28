from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.database.repository import SavedProjectRepository
from app.runner.local_prompt_schedules import LocalPromptScheduleStore


@pytest.fixture
def operator(monkeypatch, tmp_path):
    from app.config.settings import get_settings, settings

    monkeypatch.setattr(settings, "TELEGRAM_ALLOWED_USER_ID", 123)
    monkeypatch.setattr(get_settings(), "LOCAL_PROMPT_SCHEDULES_PATH", tmp_path / "default.csv")
    return 123


async def make_project(temp_db, tmp_path, owner):
    return await SavedProjectRepository(temp_db).add(owner, "sample", str(tmp_path), None)


@pytest.mark.asyncio
async def test_repository_csv_is_not_automatically_discovered(temp_db, tmp_path, operator):
    project = await make_project(temp_db, tmp_path, operator)
    store = LocalPromptScheduleStore.for_project(tmp_path)
    await store.create(f"saved:{project.id}", "sample", "external instruction", datetime.now(UTC), operator)

    assert await LocalPromptScheduleStore.discover(temp_db) == []
    assert [item.path for item in await LocalPromptScheduleStore.discover_untrusted(temp_db)] == [store.path]


@pytest.mark.asyncio
async def test_unapproved_repository_csv_never_reaches_executor(temp_db, tmp_path, operator):
    from app.runner.scheduler import JobScheduler

    project = await make_project(temp_db, tmp_path, operator)
    store = LocalPromptScheduleStore.for_project(tmp_path)
    await store.create(f"saved:{project.id}", "sample", "external instruction", datetime.now(UTC), operator)
    before = store.path.read_bytes()
    executor = SimpleNamespace(
        get_active_job=AsyncMock(return_value=None), start_local_prompt_job=AsyncMock(),
    )
    await JobScheduler()._tick({"db": temp_db, "executor": executor})
    executor.start_local_prompt_job.assert_not_awaited()
    assert store.path.read_bytes() == before


@pytest.mark.asyncio
async def test_bot_created_file_registers_only_metadata(temp_db, tmp_path, operator):
    project = await make_project(temp_db, tmp_path, operator)
    store = LocalPromptScheduleStore.for_project(tmp_path)
    await store.create(
        f"saved:{project.id}", "sample", "private prompt", datetime.now(UTC), operator, db=temp_db,
    )
    assert await store.is_trusted(temp_db, operator)
    assert [item.path for item in await LocalPromptScheduleStore.discover(temp_db)] == [store.path]
    rows = await temp_db.conn.execute_fetchall("SELECT * FROM trusted_local_schedule_stores")
    assert len(rows) == 1
    assert "private prompt" not in str(tuple(rows[0]))


@pytest.mark.asyncio
async def test_appending_to_unapproved_file_preserves_bytes(temp_db, tmp_path, operator):
    from app.runner.local_prompt_schedules import UntrustedScheduleStoreError

    project = await make_project(temp_db, tmp_path, operator)
    store = LocalPromptScheduleStore.for_project(tmp_path)
    await store.create(f"saved:{project.id}", "sample", "external", datetime.now(UTC), operator)
    before = store.path.read_bytes()
    with pytest.raises(UntrustedScheduleStoreError):
        await store.create(
            f"saved:{project.id}", "sample", "new", datetime.now(UTC), operator, db=temp_db,
        )
    assert store.path.read_bytes() == before
    assert not await store.is_trusted(temp_db, operator)


@pytest.mark.asyncio
async def test_explicit_approval_preserves_manual_csv_editing(temp_db, tmp_path, operator):
    project = await make_project(temp_db, tmp_path, operator)
    store = LocalPromptScheduleStore.for_project(tmp_path)
    saved = await store.create(f"saved:{project.id}", "sample", "first", datetime.now(UTC), operator)
    await store.trust_existing(temp_db, operator)
    store.path.write_text(store.path.read_text(encoding="utf-8").replace("first", "manually edited"), encoding="utf-8")
    discovered = await LocalPromptScheduleStore.discover(temp_db)
    assert (await discovered[0].get(saved.id)).prompt == "manually edited"


@pytest.mark.asyncio
async def test_store_approval_cannot_cross_project_owner(temp_db, tmp_path, operator):
    project = await make_project(temp_db, tmp_path, 999)
    store = LocalPromptScheduleStore.for_project(tmp_path)
    await store.create(f"saved:{project.id}", "sample", "external", datetime.now(UTC), operator)
    with pytest.raises(ValueError, match="owner"):
        await store.trust_existing(temp_db, operator)
    assert not await store.is_trusted(temp_db, operator)
    assert await LocalPromptScheduleStore.discover_untrusted(temp_db) == []


@pytest.mark.asyncio
async def test_store_approval_refuses_foreign_chat_rows(temp_db, tmp_path, operator):
    project = await make_project(temp_db, tmp_path, operator)
    store = LocalPromptScheduleStore.for_project(tmp_path)
    await store.create(f"saved:{project.id}", "sample", "external", datetime.now(UTC), 999)
    with pytest.raises(ValueError, match="owner"):
        await store.trust_existing(temp_db, operator)
    assert not await store.is_trusted(temp_db, operator)


@pytest.mark.asyncio
async def test_ensure_existing_untrusted_csv_requires_approval(temp_db, tmp_path, operator):
    from app.runner.local_prompt_schedules import UntrustedScheduleStoreError

    await make_project(temp_db, tmp_path, operator)
    store = LocalPromptScheduleStore.for_project(tmp_path)
    await store.ensure_exists()
    with pytest.raises(UntrustedScheduleStoreError):
        await store.ensure_exists(db=temp_db)
    await store.trust_existing(temp_db, operator)
    await store.ensure_exists(db=temp_db)
