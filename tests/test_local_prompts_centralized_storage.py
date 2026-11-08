"""
Tests for centralized local prompt storage and completed prompt suppression in Telegram bot.
Verifies:
1. Sched_confirm_callback saves prompts in the bot's central storage, NOT in project_path.
2. Completed/executed prompts are excluded from _local_schedules_for_chat and never appear in bot.
"""
from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.database.repository import SavedProjectRepository
from app.runner.local_prompt_schedules import LocalPromptScheduleStore, LocalPromptStatus
from app.telegram.handlers import schedule


def _update(data: str = "sched_confirm") -> SimpleNamespace:
    return SimpleNamespace(
        callback_query=SimpleNamespace(
            data=data,
            answer=AsyncMock(),
            edit_message_text=AsyncMock(),
        ),
        effective_user=SimpleNamespace(id=123456789, username="tester"),
        effective_chat=SimpleNamespace(id=123456789),
        message=None,
    )


@pytest.mark.asyncio
async def test_schedule_local_prompt_does_not_create_csv_in_project_dir(
    temp_db, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    project_dir = tmp_path / "clean_project"
    project_dir.mkdir()
    bot_workspace = tmp_path / "bot_workspace"
    bot_workspace.mkdir()

    # Configure settings to use bot_workspace for local prompt schedules
    from app.config.settings import settings
    monkeypatch.setattr(settings, "WORKSPACE_DIR", bot_workspace)
    monkeypatch.setattr(settings, "LOCAL_PROMPT_SCHEDULES_PATH", None)

    project = await SavedProjectRepository(temp_db).add(123456789, "clean_project", str(project_dir), None)
    context = SimpleNamespace(
        user_data={
            "sched_mode": "local",
            "sched_project_id": f"saved:{project.id}",
            "sched_project_name": "clean_project",
            "sched_project_path": str(project_dir),
            "sched_prompt": "Clean project prompt",
            "sched_agent": "antigravity",
            "sched_model": "default",
            "sched_effort": "high",
            "sched_dt": (datetime.now(UTC) + timedelta(hours=2)).isoformat(),
        },
        bot_data={"db": temp_db},
    )
    update = _update("sched_confirm")

    state = await schedule.sched_confirm_callback(update, context)

    # 1. Conversation ended successfully
    assert state == schedule.ConversationHandler.END

    # 2. Project directory must NOT contain local_prompt_schedules.csv
    project_csv = project_dir / "local_prompt_schedules.csv"
    assert not project_csv.exists(), "CSV must not be created inside the user's project directory!"

    # 3. Prompt was saved in the bot's central storage
    central_store = LocalPromptScheduleStore.from_settings()
    assert central_store.path.exists()
    all_schedules = await central_store.list_all()
    assert any(s.prompt == "Clean project prompt" for s in all_schedules)


@pytest.mark.asyncio
async def test_completed_local_prompts_never_appear_in_bot_schedules(
    temp_db, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    bot_workspace = tmp_path / "bot_workspace_2"
    bot_workspace.mkdir()

    from app.config.settings import settings
    monkeypatch.setattr(settings, "WORKSPACE_DIR", bot_workspace)
    monkeypatch.setattr(settings, "LOCAL_PROMPT_SCHEDULES_PATH", None)

    store = LocalPromptScheduleStore.from_settings()
    now = datetime.now(UTC)

    # Create a pending prompt
    pending_sched = await store.create(
        project_id="saved:1",
        project_name="project1",
        prompt="Pending task that has not run",
        scheduled_at=now + timedelta(hours=1),
        chat_id=123456789,
        db=temp_db,
    )

    # Create an executed/completed prompt
    completed_sched = await store.create(
        project_id="saved:1",
        project_name="project1",
        prompt="Completed task that finished",
        scheduled_at=now - timedelta(hours=1),
        chat_id=123456789,
        db=temp_db,
    )
    await store.mark_launched(completed_sched.id)
    await store.mark_completed(completed_sched.id)

    # Verify directly in store
    assert (await store.get(pending_sched.id)).status == LocalPromptStatus.PENDING
    assert (await store.get(completed_sched.id)).status == LocalPromptStatus.COMPLETED

    # Now verify in Telegram bot's schedule query for chat
    context = SimpleNamespace(bot_data={"db": temp_db})
    bot_visible_schedules = await schedule._local_schedules_for_chat(context, 123456789)

    # The completed task MUST NOT appear in the bot!
    visible_ids = [s.id for s in bot_visible_schedules]
    assert pending_sched.id in visible_ids
    assert completed_sched.id not in visible_ids, "Executed prompt must never appear in the bot!"
