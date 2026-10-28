from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from app.runner.local_projects import LocalProjectRegistry
from app.runner.local_prompt_schedules import LocalPromptScheduleStore, LocalPromptStatus
from app.runner.project_scanner import ProjectScanner


def _make_git_project(path: Path) -> None:
    git_dir = path / ".git"
    git_dir.mkdir(parents=True)
    (git_dir / "HEAD").write_text("ref: refs/heads/main\n", encoding="utf-8")
    (git_dir / "config").write_text(
        '[remote "origin"]\n\turl = https://github.com/acme/example.git\n',
        encoding="utf-8",
    )


def test_project_registry_resolves_project_after_move(tmp_path: Path):
    projects_root = tmp_path / "projects"
    original = projects_root / "first-location"
    _make_git_project(original)

    scanner = ProjectScanner()
    project = scanner.inspect_directory(original)
    registry = LocalProjectRegistry(projects_root)

    registered = registry.register(project)
    moved = projects_root / "new-location"
    original.rename(moved)

    resolved = registry.resolve(registered.project_id)

    assert resolved is not None
    assert resolved.path == moved.resolve()
    assert resolved.repo_full_name == "acme/example"


@pytest.mark.asyncio
async def test_csv_schedule_preserves_prompt_and_allows_pending_time_edit(tmp_path: Path):
    store = LocalPromptScheduleStore(tmp_path / "local_prompt_schedules.csv")
    original_time = datetime.now(UTC) + timedelta(hours=1)
    prompt = "افحص الاختبارات أولاً.\n\nلا تعدل ملفات الإعدادات."

    scheduled = await store.create(
        project_id="project-1",
        project_name="example",
        prompt=prompt,
        scheduled_at=original_time,
        chat_id=123,
    )

    updated_time = original_time + timedelta(hours=2)
    updated = await store.update_time(scheduled.id, updated_time)
    loaded = await store.get(scheduled.id)

    assert updated is True
    assert loaded is not None
    assert loaded.prompt == prompt
    assert loaded.scheduled_at == updated_time.strftime("%Y-%m-%dT%H:%M:%SZ")
    assert loaded.status == LocalPromptStatus.PENDING


@pytest.mark.asyncio
async def test_csv_schedule_is_created_before_the_first_schedule(tmp_path: Path):
    path = tmp_path / "local_prompt_schedules.csv"
    store = LocalPromptScheduleStore(path)

    await store.ensure_exists()

    assert path.exists()
    assert await store.list_all() == []


def test_project_schedule_csv_is_stored_beside_the_project(tmp_path: Path):
    store = LocalPromptScheduleStore.for_project(tmp_path)

    assert store.path == (tmp_path / "local_prompt_schedules.csv").resolve()


@pytest.mark.asyncio
async def test_csv_schedule_can_fail_before_it_launches(tmp_path: Path):
    store = LocalPromptScheduleStore(tmp_path / "local_prompt_schedules.csv")
    scheduled = await store.create(
        project_id="missing-project",
        project_name="missing",
        prompt="نفذ الاختبارات",
        scheduled_at=datetime.now(UTC) + timedelta(hours=1),
        chat_id=123,
    )

    assert await store.mark_failed(scheduled.id, "Project was not found") is True
    loaded = await store.get(scheduled.id)
    assert loaded is not None
    assert loaded.status == LocalPromptStatus.FAILED
    assert loaded.last_error == "Project was not found"


@pytest.mark.asyncio
async def test_invalid_csv_date_is_reported_as_value_error(tmp_path: Path):
    store = LocalPromptScheduleStore.for_project(tmp_path)
    schedule = await store.create(
        project_id="saved:1", project_name="sample", prompt="run tests",
        scheduled_at=datetime.now(UTC), chat_id=123,
    )
    rows = store._read_rows()
    rows[0]["scheduled_at"] = "invalid-date"
    store._write_rows(rows)

    with pytest.raises(ValueError, match="invalid schedule row"):
        await store.list_due()
    assert schedule.id


@pytest.mark.asyncio
async def test_csv_schedules_sort_by_instant_not_timezone_text(tmp_path: Path):
    store = LocalPromptScheduleStore.for_project(tmp_path)
    for prompt in ("first", "second"):
        await store.create(
            project_id="saved:1", project_name="sample", prompt=prompt,
            scheduled_at=datetime.now(UTC), chat_id=123,
        )
    rows = store._read_rows()
    rows[0]["scheduled_at"] = "2026-10-07T10:00:00+03:00"
    rows[1]["scheduled_at"] = "2026-10-07T08:00:00Z"
    store._write_rows(rows)

    assert [item.prompt for item in await store.list_all()] == ["first", "second"]
