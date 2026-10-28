from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.database.schedule_repository import ScheduleRepository
from app.telegram.handlers import schedule


def _update(data):
    return SimpleNamespace(
        callback_query=SimpleNamespace(data=data, answer=AsyncMock(), edit_message_text=AsyncMock()),
        effective_user=SimpleNamespace(id=123456789, username=None),
        effective_chat=SimpleNamespace(id=123456789), message=None,
    )


@pytest.mark.parametrize(
    ("handler", "prefix"),
    [
        ("sched_open_callback", "sched_open:"),
        ("sched_delete_callback", "sched_del:"),
        ("sched_start_now_callback", "sched_now:"),
        ("sched_issue_edit_time_start", "sched_time:"),
    ],
)
async def test_issue_schedule_actions_reject_foreign_chat(temp_db, monkeypatch, handler, prefix):
    job = await ScheduleRepository(temp_db).create(
        "foreign/private", 1, "codex", datetime.now(UTC) + timedelta(days=1), 999,
    )
    monkeypatch.setattr(schedule, "_local_schedule_stores", AsyncMock(return_value=[]))
    monkeypatch.setattr(schedule.LocalPromptScheduleStore, "discover_untrusted", AsyncMock(return_value=[]))
    context = SimpleNamespace(bot_data={"db": temp_db}, user_data={})
    update = _update(f"{prefix}{job.id}")

    await getattr(schedule, handler)(update, context)

    assert await ScheduleRepository(temp_db).get(job.id) == job
    assert "sched_edit_issue_id" not in context.user_data
    for call in update.callback_query.edit_message_text.call_args_list:
        assert "foreign/private" not in str(call)


async def test_issue_edit_received_rechecks_owner_before_mutation(temp_db, monkeypatch):
    job = await ScheduleRepository(temp_db).create(
        "foreign/private", 1, "codex", datetime.now(UTC) + timedelta(days=1), 999,
    )
    monkeypatch.setattr(schedule, "_parse_user_time", lambda *_: datetime.now(UTC) + timedelta(days=2))
    context = SimpleNamespace(bot_data={"db": temp_db}, user_data={"sched_edit_issue_id": job.id})
    update = _update("")
    update.callback_query = None
    update.message = SimpleNamespace(text="tomorrow", reply_text=AsyncMock())

    await schedule.sched_issue_edit_time_received(update, context)

    assert await ScheduleRepository(temp_db).get(job.id) == job
    assert "تم تعديل الموعد" not in update.message.reply_text.call_args.args[0]


async def test_local_schedule_lookup_skips_foreign_chat_even_when_id_matches(monkeypatch):
    foreign = SimpleNamespace(id="same-id", chat_id=999)
    own = SimpleNamespace(id="same-id", chat_id=123456789)
    foreign_store = SimpleNamespace(get=AsyncMock(return_value=foreign))
    own_store = SimpleNamespace(get=AsyncMock(return_value=own))
    monkeypatch.setattr(
        schedule, "_local_schedule_stores", AsyncMock(return_value=[foreign_store, own_store]),
    )
    context = SimpleNamespace(bot_data={}, user_data={})

    assert await schedule._find_local_schedule(context, "same-id", 123456789) == (own_store, own)


async def test_local_edit_start_rejects_foreign_chat(monkeypatch):
    foreign_store = SimpleNamespace(get=AsyncMock(return_value=SimpleNamespace(chat_id=999)))
    monkeypatch.setattr(schedule, "_local_schedule_stores", AsyncMock(return_value=[foreign_store]))
    monkeypatch.setattr(schedule, "_show_all_schedules", AsyncMock())
    context = SimpleNamespace(bot_data={}, user_data={})

    await schedule.sched_local_edit_time_start(_update("local_sched_time:foreign"), context)

    assert "sched_edit_local_id" not in context.user_data
