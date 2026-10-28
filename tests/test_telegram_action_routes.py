from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest
from telegram import CallbackQuery, Chat, Message, Update, User
from telegram.ext import CallbackQueryHandler, ConversationHandler, MessageHandler

from app.database.repository import SavedProjectRepository
from app.telegram.handlers import personal, run
from app.telegram.keyboards import MAIN_MENU_STATUS, personal_keyboard


def _query_update(data: str):
    return SimpleNamespace(
        callback_query=SimpleNamespace(data=data, answer=AsyncMock(), edit_message_text=AsyncMock()),
        message=None, effective_user=SimpleNamespace(id=123456789, username=None),
    )


@pytest.mark.asyncio
async def test_saved_project_run_entry_is_registered_and_preserves_local_path(temp_db, tmp_path, monkeypatch):
    folder = tmp_path / "sample"
    (folder / ".git").mkdir(parents=True)
    saved = await SavedProjectRepository(temp_db).add(123456789, "sample", str(folder), "acme/sample")
    context = SimpleNamespace(user_data={}, bot_data={"db": temp_db})
    update = _query_update(f"run_saved_proj:{saved.id}")
    handler = run.build_run_handler()
    entry = next((item for item in handler.entry_points if isinstance(item, CallbackQueryHandler)
                  and item.pattern.match(update.callback_query.data)), None)
    assert entry is not None
    issues = AsyncMock(return_value=run.RUN_SELECT_ISSUE)
    monkeypatch.setattr(run, "_show_run_issues", issues)

    assert await entry.callback(update, context) == run.RUN_SELECT_ISSUE
    assert context.user_data["run_local_path"] == str(folder.resolve())
    assert context.user_data["run_repo"] == "acme/sample"
    issues.assert_awaited_once_with(update, context, "acme/sample", page=0)


@pytest.mark.asyncio
@pytest.mark.parametrize("wrong_owner", [False, True])
async def test_saved_run_rejects_missing_path_or_different_owner(temp_db, tmp_path, monkeypatch, wrong_owner):
    folder = tmp_path / "missing"
    if wrong_owner:
        (folder / ".git").mkdir(parents=True)
    saved = await SavedProjectRepository(temp_db).add(
        999 if wrong_owner else 123456789, "sample", str(folder), "acme/sample",
    )
    context = SimpleNamespace(user_data={}, bot_data={"db": temp_db})
    update = _query_update(f"run_saved_proj:{saved.id}")
    handler = run.build_run_handler()
    entry = next((item for item in handler.entry_points if isinstance(item, CallbackQueryHandler)
                  and item.pattern.match(update.callback_query.data)), None)
    assert entry is not None
    issues = AsyncMock()
    monkeypatch.setattr(run, "_show_run_issues", issues)

    assert await entry.callback(update, context) == ConversationHandler.END
    issues.assert_not_awaited()
    assert not context.user_data.get("run_local_path")


@pytest.mark.asyncio
async def test_run_for_repo_does_not_mutate_immutable_telegram_query(monkeypatch):
    query = CallbackQuery("1", User(123456789, "tester", False), "chat", data="run_for:acme/example")
    update = SimpleNamespace(callback_query=query, effective_user=SimpleNamespace(id=123456789, username=None))
    context = SimpleNamespace(user_data={"run_local_path": "stale project"})
    show = AsyncMock(return_value=run.RUN_SELECT_ISSUE)
    monkeypatch.setattr(run, "_show_run_issues", show)
    monkeypatch.setattr(CallbackQuery, "answer", AsyncMock())

    assert await run._run_for_repo(update, context) == run.RUN_SELECT_ISSUE
    assert query.data == "run_for:acme/example"
    assert context.user_data.get("run_local_path") is None
    show.assert_awaited_once_with(update, context, "acme/example", page=0)


@pytest.mark.asyncio
async def test_run_main_menu_ends_conversation_and_clears_only_run_state(monkeypatch):
    update = Update(1, message=Message(
        1, datetime.now(UTC), Chat(1, "private"), from_user=User(123456789, "tester", False),
        text=MAIN_MENU_STATUS,
    ))
    context = SimpleNamespace(user_data={"run_repo": "old", "directory_browser_current": "old", "keep": 1})
    handler = run.build_run_handler()
    target = next((item for item in handler.fallbacks if isinstance(item, MessageHandler)
                   and item.check_update(update)), None)
    assert target is not None
    routed = AsyncMock()
    monkeypatch.setattr("app.telegram.handlers.start.main_menu_message_handler", routed)

    assert await target.callback(update, context) == ConversationHandler.END
    assert context.user_data == {"keep": 1}
    routed.assert_awaited_once_with(update, context)


@pytest.mark.asyncio
async def test_personal_action_buttons_are_registered(monkeypatch):
    from app.telegram.bot import build_application

    app = build_application("123456789:mocktoken")
    callbacks = [button.callback_data for row in personal_keyboard().inline_keyboard for button in row
                 if str(button.callback_data).startswith("personal:")]
    handlers = [item for group in app.handlers.values() for item in group
                if isinstance(item, CallbackQueryHandler)]
    for action in callbacks:
        assert any(item.pattern and item.pattern.match(action) for item in handlers), action


@pytest.mark.asyncio
@pytest.mark.parametrize("action", ["task", "getfile", "ls", "shell"])
async def test_personal_text_actions_show_guarded_command_help(action, monkeypatch):
    route = getattr(personal, "personal_action_callback", None)
    assert route is not None
    monkeypatch.setattr(personal, "_check_personal_guard", lambda: (True, ""))
    context = SimpleNamespace(user_data={})
    update = _query_update(f"personal:{action}")
    task = AsyncMock()
    shell = AsyncMock()
    monkeypatch.setattr(personal.personal_task_manager, "run_task", task)
    monkeypatch.setattr(personal, "_run_shell", shell)

    await route(update, context)

    assert f"/{action}" in update.callback_query.edit_message_text.call_args.args[0]
    task.assert_not_awaited()
    shell.assert_not_awaited()


@pytest.mark.asyncio
async def test_personal_button_cannot_bypass_kill_switch(monkeypatch):
    route = getattr(personal, "personal_action_callback", None)
    assert route is not None
    monkeypatch.setattr(personal, "_check_personal_guard", lambda: (False, "locked"))
    update = _query_update("personal:ps")
    get_processes = Mock(side_effect=AssertionError("disabled personal mode"))
    monkeypatch.setattr("psutil.process_iter", get_processes)

    await route(update, SimpleNamespace(user_data={}))

    assert update.callback_query.edit_message_text.call_args.args[0] == "locked"
    get_processes.assert_not_called()


@pytest.mark.asyncio
async def test_upload_action_uses_bounded_token_not_filename(tmp_path: Path, monkeypatch):
    long_name = "uploaded-file-" + "x" * 150 + ".txt"
    saved = tmp_path / long_name
    monkeypatch.setattr(personal, "_check_personal_guard", lambda: (True, ""))
    monkeypatch.setattr(personal, "save_incoming_file", lambda **kwargs: (True, "saved", saved))
    status = SimpleNamespace(edit_text=AsyncMock())
    message = SimpleNamespace(
        document=SimpleNamespace(file_name=long_name, file_id="1"),
        reply_text=AsyncMock(return_value=status),
    )
    update = SimpleNamespace(message=message, effective_user=SimpleNamespace(id=123456789, username=None))
    file = SimpleNamespace(download_as_bytearray=AsyncMock(return_value=b"test"))
    context = SimpleNamespace(user_data={}, bot=SimpleNamespace(get_file=AsyncMock(return_value=file)))

    await personal.incoming_file_handler(update, context)

    markup = status.edit_text.call_args.kwargs["reply_markup"]
    actions = [button.callback_data for row in markup.inline_keyboard for button in row
               if str(button.callback_data).startswith("personal:task_")]
    assert len(actions) == 1
    assert len(actions[0].encode("utf-8")) <= 64
    assert long_name not in actions[0]
    assert str(saved) in context.user_data["personal_uploads"].values()

    selected = _query_update(actions[0])
    route = getattr(personal, "personal_action_callback", None)
    assert route is not None
    await route(selected, context)
    assert str(saved) in selected.callback_query.edit_message_text.call_args.args[0]
