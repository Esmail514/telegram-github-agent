from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.database.repository import SavedProjectRepository, SelectedProjectRepository
from app.telegram.handlers import projects


@pytest.mark.parametrize(
    ("handler_name", "prefix"),
    [
        ("saved_proj_selected_callback", "saved_proj:"),
        ("saved_proj_delete_callback", "saved_proj_del:"),
        ("saved_proj_delete_confirm_callback", "saved_proj_del_confirm:"),
    ],
)
async def test_foreign_saved_project_callbacks_never_select_delete_or_disclose(
    temp_db, handler_name, prefix,
):
    foreign = await SavedProjectRepository(temp_db).add(
        999, "foreign-private-project", "D:/foreign/private-project", "foreign/private-repo",
    )
    await SelectedProjectRepository(temp_db).save(123456789, "own", "D:/own", None)
    query = SimpleNamespace(
        data=f"{prefix}{foreign.id}", answer=AsyncMock(), edit_message_text=AsyncMock(),
    )
    update = SimpleNamespace(
        callback_query=query, message=None,
        effective_user=SimpleNamespace(id=123456789, username=None),
    )
    context = SimpleNamespace(bot_data={"db": temp_db}, user_data={"run_local_path": "D:/own"})

    await getattr(projects, handler_name)(update, context)

    assert await SavedProjectRepository(temp_db).get_by_id(foreign.id) is not None
    selected = await SelectedProjectRepository(temp_db).get(123456789)
    assert selected is not None and selected.path == "D:/own"
    assert context.user_data == {"run_local_path": "D:/own"}
    for call in query.answer.call_args_list + query.edit_message_text.call_args_list:
        assert "foreign" not in str(call)
    assert any(call.kwargs.get("show_alert") for call in query.answer.call_args_list)
