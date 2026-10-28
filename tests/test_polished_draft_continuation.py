from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest
from telegram.ext import ConversationHandler

from app.telegram.handlers import newissue, polishissue, start
from app.telegram.keyboards import MAIN_MENU_GITHUB


def _polish_context():
    return SimpleNamespace(user_data={
        "ni_repo": "acme/example", "ni_title": "old title", "ni_body": "old body",
        "polish_repo": "acme/example", "polish_source": "newissue", "polish_issue_num": None,
        "polish_title": "fix: improved _title_", "polish_body": "improved body", "polish_labels": ["bug"],
        "unrelated": 123,
    })


def _query_update(action):
    return SimpleNamespace(
        callback_query=SimpleNamespace(data=action, answer=AsyncMock(), edit_message_text=AsyncMock()),
        message=None, effective_user=SimpleNamespace(id=123456789, username=None),
    )


@pytest.mark.asyncio
async def test_applying_polished_draft_returns_to_real_confirmation_and_creates_improved_issue(monkeypatch):
    context = _polish_context()
    update = _query_update("polish:apply")

    await polishissue.polish_apply_callback(update, context)

    call = update.callback_query.edit_message_text.call_args
    assert "_title_" in context.user_data["ni_title"]
    assert context.user_data["ni_body"] == "improved body"
    assert not any(key.startswith("polish_") for key in context.user_data)
    assert context.user_data["unrelated"] == 123
    keyboard = call.kwargs["reply_markup"]
    assert "newissue:confirm" in [button.callback_data for row in keyboard.inline_keyboard for button in row]
    assert "/newissue" not in call.args[0]

    create = Mock(return_value=SimpleNamespace(number=7, title="fix: improved title"))
    monkeypatch.setattr(newissue.issue_service, "create_issue", create)
    assert await newissue.ni_confirm(_query_update("newissue:confirm"), context) == ConversationHandler.END
    create.assert_called_once_with("acme/example", "fix: improved _title_", "improved body", ["bug"])


@pytest.mark.asyncio
@pytest.mark.parametrize("callback", [newissue.ni_main_menu, newissue.ni_to_menu, newissue.ni_cancel])
async def test_draft_exit_clears_only_draft_and_polish_state(callback, monkeypatch):
    context = _polish_context()
    monkeypatch.setattr(start, "main_menu_message_handler", AsyncMock())

    assert await callback(_query_update("menu:start"), context) == ConversationHandler.END

    assert context.user_data == {"unrelated": 123}


@pytest.mark.asyncio
async def test_global_bottom_menu_clears_polish_feedback_without_touching_other_state():
    context = _polish_context()
    expected = {key: value for key, value in context.user_data.items() if not key.startswith("polish_")}
    message = SimpleNamespace(text=MAIN_MENU_GITHUB, reply_text=AsyncMock())
    update = SimpleNamespace(message=message, effective_user=SimpleNamespace(id=123456789, username=None))

    await start.main_menu_message_handler(update, context)

    assert context.user_data == expected
    message.reply_text.assert_awaited_once()
