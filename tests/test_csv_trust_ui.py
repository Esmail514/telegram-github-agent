from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from telegram.ext import CallbackQueryHandler

from app.database.repository import SavedProjectRepository
from app.runner.local_prompt_schedules import LocalPromptScheduleStore
from app.telegram.handlers import schedule


def _update(data="sched_list"):
    return SimpleNamespace(
        callback_query=SimpleNamespace(data=data, answer=AsyncMock(), edit_message_text=AsyncMock()),
        effective_user=SimpleNamespace(id=123456789, username=None),
        effective_chat=SimpleNamespace(id=123456789), message=None,
    )


async def _existing_file(temp_db, tmp_path):
    project = await SavedProjectRepository(temp_db).add(123456789, "sample", str(tmp_path), None)
    store = LocalPromptScheduleStore.for_project(tmp_path)
    await store.create(f"saved:{project.id}", "sample", "Review existing prompt", datetime.now(UTC), 123456789)
    return store


@pytest.mark.asyncio
async def test_untrusted_csv_requires_preview_then_explicit_confirmation(temp_db, tmp_path):
    store = await _existing_file(temp_db, tmp_path)
    context = SimpleNamespace(user_data={}, bot_data={"db": temp_db})
    update = _update()
    await schedule.schedule_list_command(update, context)
    keyboard = update.callback_query.edit_message_text.call_args.kwargs["reply_markup"]
    button = next(button for row in keyboard.inline_keyboard for button in row
                  if (button.callback_data or "").startswith("csv_trust:"))
    assert not await store.is_trusted(temp_db, 123456789)

    update.callback_query.data = button.callback_data
    await schedule.csv_trust_preview_callback(update, context)
    assert "Review existing prompt" in update.callback_query.edit_message_text.call_args.args[0]
    assert not await store.is_trusted(temp_db, 123456789)

    update.callback_query.data = "csv_trust_confirm"
    await schedule.csv_trust_confirm_callback(update, context)
    assert await store.is_trusted(temp_db, 123456789)
    assert store.path in {item.path for item in await LocalPromptScheduleStore.discover(temp_db)}


@pytest.mark.asyncio
async def test_csv_preview_rejects_path_outside_owned_projects(temp_db, tmp_path):
    store = LocalPromptScheduleStore.for_project(tmp_path)
    await store.create("saved:1", "sample", "unowned", datetime.now(UTC), 123456789)
    update = _update(f"csv_trust:{store.path}")
    context = SimpleNamespace(user_data={}, bot_data={"db": temp_db})
    await schedule.csv_trust_preview_callback(update, context)
    assert not context.user_data.get("csv_trust_pending")
    assert not await store.is_trusted(temp_db, 123456789)


def test_csv_trust_callbacks_are_available_during_schedule_confirmation():
    handler = schedule.build_schedule_handler()
    for value in ("csv_trust_confirm", "csv_trust_back"):
        assert any(isinstance(item, CallbackQueryHandler) and item.pattern.match(value)
                   for item in handler.fallbacks)


@pytest.mark.asyncio
async def test_csv_trust_confirmation_retains_and_saves_original_schedule(temp_db, tmp_path):
    store = await _existing_file(temp_db, tmp_path)
    project = (await SavedProjectRepository(temp_db).list_all_projects())[0]
    context = SimpleNamespace(user_data={
        "sched_mode": "local", "sched_project_id": f"saved:{project.id}",
        "sched_project_name": "sample", "sched_project_path": str(tmp_path),
        "sched_prompt": "New operator prompt", "sched_agent": "codex",
        "sched_model": "gpt-5.6-terra", "sched_effort": "high",
        "sched_dt": (datetime.now(UTC) + timedelta(hours=1)).isoformat(),
    }, bot_data={"db": temp_db})
    update = _update("sched_confirm")
    original = store.path.read_bytes()

    assert await schedule.sched_confirm_callback(update, context) == schedule.SCHED_CONFIRM
    assert store.path.read_bytes() == original
    assert context.user_data["sched_prompt"] == "New operator prompt"
    update.callback_query.data = "csv_trust_confirm"
    await schedule.csv_trust_confirm_callback(update, context)

    assert await store.is_trusted(temp_db, 123456789)
    assert {item.prompt for item in await store.list_all()} == {
        "Review existing prompt", "New operator prompt",
    }


@pytest.mark.asyncio
async def test_back_from_csv_preview_does_not_approve_or_modify_file(temp_db, tmp_path):
    store = await _existing_file(temp_db, tmp_path)
    context = SimpleNamespace(user_data={}, bot_data={"db": temp_db})
    update = _update(f"csv_trust:{store.path}")
    before = store.path.read_bytes()
    await schedule.csv_trust_preview_callback(update, context)
    await schedule.csv_trust_back_callback(update, context)
    assert "csv_trust_pending" not in context.user_data
    assert not await store.is_trusted(temp_db, 123456789)
    assert store.path.read_bytes() == before
