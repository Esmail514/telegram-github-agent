from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from telegram import Chat, Message, Update, User
from telegram.ext import ConversationHandler, MessageHandler

from app.telegram.handlers import newissue, projects
from app.telegram.keyboards import (
    MAIN_MENU_GITHUB,
    MAIN_MENU_PERSONAL,
    MAIN_MENU_PROJECTS,
    MAIN_MENU_SCHEDULES,
    MAIN_MENU_STATUS,
    MAIN_MENU_SYSTEM,
)

_MENU_LABELS = (MAIN_MENU_GITHUB, MAIN_MENU_PERSONAL, MAIN_MENU_PROJECTS,
                MAIN_MENU_SCHEDULES, MAIN_MENU_STATUS, MAIN_MENU_SYSTEM)


def _message_update(text: str) -> Update:
    return Update(update_id=1, message=Message(
        message_id=1, date=datetime.now(UTC), chat=Chat(id=1, type="private"),
        from_user=User(id=123456789, first_name="tester", is_bot=False), text=text,
    ))


@pytest.mark.parametrize("label", _MENU_LABELS)
@pytest.mark.parametrize("state", [newissue.NI_ENTER_TITLE, newissue.NI_ENTER_BODY,
                                  newissue.NI_ENTER_LABELS])
def test_main_menu_text_is_not_consumed_as_issue_content(label, state):
    handler = newissue.build_newissue_handler()
    update = _message_update(label)

    content_handlers = [item for item in handler.states[state] if isinstance(item, MessageHandler)]
    assert not any(item.check_update(update) for item in content_handlers)
    assert any(isinstance(item, MessageHandler) and item.check_update(update)
               for item in handler.fallbacks)


@pytest.mark.asyncio
@pytest.mark.parametrize("label", _MENU_LABELS)
async def test_newissue_main_menu_fallback_clears_draft_and_dispatches(label, monkeypatch):
    context = SimpleNamespace(user_data={"ni_title": "draft", "ni_body": "private", "keep": 1})
    update = _message_update(label)
    handler = newissue.build_newissue_handler()
    target = next((item for item in handler.fallbacks if isinstance(item, MessageHandler)
                   and item.check_update(update)), None)
    assert target is not None
    routed = AsyncMock()
    monkeypatch.setattr("app.telegram.handlers.start.main_menu_message_handler", routed)

    assert await target.callback(update, context) == ConversationHandler.END
    routed.assert_awaited_once_with(update, context)
    assert context.user_data == {"keep": 1}


@pytest.mark.parametrize("builder", [projects.build_addproj_handler, projects.build_setdir_handler])
@pytest.mark.asyncio
async def test_project_browser_main_menu_fallback_ends_stale_conversation(builder, monkeypatch):
    context = SimpleNamespace(user_data={"directory_browser_current": "stale", "keep": 1})
    update = _message_update(MAIN_MENU_STATUS)
    handler = builder()
    target = next((item for item in handler.fallbacks if isinstance(item, MessageHandler)
                   and item.check_update(update)), None)
    assert target is not None
    routed = AsyncMock()
    monkeypatch.setattr("app.telegram.handlers.start.main_menu_message_handler", routed)

    assert await target.callback(update, context) == ConversationHandler.END
    assert context.user_data == {"keep": 1}
    routed.assert_awaited_once_with(update, context)
