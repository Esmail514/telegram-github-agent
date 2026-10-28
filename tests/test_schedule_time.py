from datetime import UTC, datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from telegram.ext import CallbackQueryHandler

from app.telegram.handlers.schedule import _parse_user_time, _schedule_time_hint


def test_time_shortcuts_do_not_match_schedule_edit_entry_point():
    from app.telegram.handlers.schedule import build_schedule_handler
    handler = build_schedule_handler()
    edit_handlers = [item for item in handler.entry_points if isinstance(item, CallbackQueryHandler)]
    assert not any(item.pattern.match("sched_time:now") for item in edit_handlers)


def test_schedule_time_uses_the_device_timezone(monkeypatch) -> None:
    device_time = datetime(2026, 10, 1, 14, 30, tzinfo=timezone(timedelta(hours=3)))
    monkeypatch.setattr("app.telegram.handlers.schedule._device_now", lambda: device_time)

    scheduled = _parse_user_time("01/10 15:30", device_time.year)

    assert scheduled == datetime(2026, 10, 1, 12, 30, tzinfo=UTC)
    hint = _schedule_time_hint()
    assert "14:30" in hint
    assert "15:30" in hint
    assert "DD/MM" not in hint


@pytest.mark.asyncio
async def test_long_prompt_is_delivered_in_full_without_breaking_confirmation():
    from app.telegram.handlers.schedule import _show_confirmation

    prompt = "test _markdown_ `characters` and [links]\n" * 220
    query = SimpleNamespace(answer=AsyncMock(), edit_message_text=AsyncMock())
    update = SimpleNamespace(callback_query=query, effective_chat=SimpleNamespace(id=123))
    context = SimpleNamespace(
        bot=SimpleNamespace(send_document=AsyncMock()),
        user_data={
            "sched_mode": "local", "sched_dt": datetime.now(UTC).isoformat(),
            "sched_prompt": prompt, "sched_agent": "codex", "sched_effort": "high",
            "sched_project_name": "sample",
        },
    )
    await _show_confirmation(update, context)

    assert len(query.edit_message_text.call_args.args[0]) < 4096
    document = context.bot.send_document.call_args.kwargs["document"]
    assert document.getvalue().decode("utf-8") == prompt


@pytest.mark.asyncio
async def test_time_choice_advances_to_agent_and_preserves_prompt():
    from app.telegram.handlers.schedule import SCHED_SELECT_AGENT, sched_time_choice_callback

    query = SimpleNamespace(data="sched_time:now", answer=AsyncMock(), edit_message_text=AsyncMock())
    update = SimpleNamespace(callback_query=query, effective_user=SimpleNamespace(id=123456789))
    context = SimpleNamespace(user_data={"sched_mode": "local", "sched_prompt": "Run tests"})

    assert await sched_time_choice_callback(update, context) == SCHED_SELECT_AGENT
    assert context.user_data["sched_prompt"] == "Run tests"
    assert context.user_data["sched_run_now"] is True


@pytest.mark.asyncio
async def test_local_schedule_prompts_with_project_information(temp_db):
    from app.database.repository import SavedProjectRepository
    from app.telegram.handlers.schedule import (
        SCHED_ENTER_PROMPT,
        SCHED_ENTER_TIME,
        _ask_for_time,
        sched_project_selected_callback,
    )

    proj = await SavedProjectRepository(temp_db).add(123456789, "my_cool_project", "D:/Work/my_cool_project", None)
    query = SimpleNamespace(data="sched_project:0", answer=AsyncMock(), edit_message_text=AsyncMock())
    update = SimpleNamespace(callback_query=query, effective_user=SimpleNamespace(id=123456789))
    context = SimpleNamespace(
        bot_data={"db": temp_db},
        user_data={"sched_mode": "local", "sched_projects": [proj]},
    )

    state = await sched_project_selected_callback(update, context)
    assert state == SCHED_ENTER_PROMPT
    assert context.user_data["sched_project_name"] == "my_cool_project"
    prompt_text = query.edit_message_text.call_args.args[0]
    assert "المشروع المستهدف" in prompt_text
    assert "my_cool_project" in prompt_text

    time_query = SimpleNamespace(answer=AsyncMock(), edit_message_text=AsyncMock())
    time_update = SimpleNamespace(callback_query=time_query, effective_user=SimpleNamespace(id=123456789))
    time_state = await _ask_for_time(time_update, context)
    assert time_state == SCHED_ENTER_TIME
    time_text = time_query.edit_message_text.call_args.args[0]
    assert "my_cool_project" in time_text



@pytest.mark.asyncio
async def test_prompt_received_shows_target_project_and_prompt_snippet(temp_db):
    from app.telegram.handlers.schedule import (
        SCHED_ENTER_TIME,
        sched_prompt_received,
    )

    update = SimpleNamespace(
        message=SimpleNamespace(text="Fix login form validation", reply_text=AsyncMock()),
        effective_user=SimpleNamespace(id=123456789),
        callback_query=None,
    )
    context = SimpleNamespace(
        bot_data={"db": temp_db},
        user_data={
            "sched_mode": "local",
            "sched_project_name": "alpha_project",
            "sched_project_path": "D:/Work/alpha",
        },
    )

    state = await sched_prompt_received(update, context)
    assert state == SCHED_ENTER_TIME
    assert context.user_data["sched_prompt"] == "Fix login form validation"

    sent_text = update.message.reply_text.call_args.args[0]
    assert "المشروع المستهدف" in sent_text
    assert "alpha_project" in sent_text
    assert "Fix login form validation" in sent_text


@pytest.mark.asyncio
async def test_ask_for_prompt_without_project_redirects_to_project_selection(temp_db):
    from app.database.repository import SavedProjectRepository
    from app.telegram.handlers.schedule import (
        SCHED_SELECT_PROJECT,
        _ask_for_prompt,
    )

    await SavedProjectRepository(temp_db).add(123456789, "proj1", "D:/Work/proj1", None)

    query = SimpleNamespace(answer=AsyncMock(), edit_message_text=AsyncMock())
    update = SimpleNamespace(callback_query=query, effective_user=SimpleNamespace(id=123456789))
    context = SimpleNamespace(bot_data={"db": temp_db}, user_data={"sched_mode": "local"})

    state = await _ask_for_prompt(update, context)
    assert state == SCHED_SELECT_PROJECT
    assert "اختر المشروع المحلي" in query.edit_message_text.call_args.args[0]
