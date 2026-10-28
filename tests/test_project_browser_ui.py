from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from app.database.repository import SavedProjectRepository
from app.runner.directory_browser import DirectoryError, DirectoryListing
from app.telegram import directory_browser as browser_ui
from app.telegram.handlers import projects


def _query_update(action: str):
    query = SimpleNamespace(data=action, answer=AsyncMock(), edit_message_text=AsyncMock())
    return SimpleNamespace(
        callback_query=query, message=None,
        effective_user=SimpleNamespace(id=123456789, username=None),
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("select_name", "confirm_name"),
    [("addproj_git_proj_callback", "addproj_confirm_callback"),
     ("setdir_git_proj_callback", "setdir_confirm_callback")],
)
async def test_git_shortcut_navigates_without_saving_until_confirm(
    temp_db, tmp_path: Path, monkeypatch, select_name: str, confirm_name: str,
):
    folder = tmp_path / "project with spaces"
    (folder / ".git").mkdir(parents=True)
    context = SimpleNamespace(
        user_data={
            "directory_browser_current": str(tmp_path),
            "directory_browser_git_projects": [str(folder)],
        },
        bot_data={"db": temp_db},
    )
    monkeypatch.setattr("subprocess.run", Mock(return_value=SimpleNamespace(returncode=1)))
    update = _query_update("dir:gitproj:0")

    state = await getattr(projects, select_name)(update, context)

    assert state == projects.ADDPROJ_BROWSE
    assert await SavedProjectRepository(temp_db).list_projects(123456789) == []
    assert browser_ui.current_directory(context) == folder.resolve()
    markup = update.callback_query.edit_message_text.call_args.kwargs["reply_markup"]
    assert any(button.callback_data == "dir:confirm" for row in markup.inline_keyboard for button in row)

    await getattr(projects, confirm_name)(_query_update("dir:confirm"), context)
    saved = await SavedProjectRepository(temp_db).list_projects(123456789)
    assert len(saved) == 1
    assert saved[0].path == str(folder.resolve())


@pytest.mark.asyncio
@pytest.mark.parametrize("select_name", ["addproj_git_proj_callback", "setdir_git_proj_callback"])
async def test_disappeared_git_shortcut_never_saves(temp_db, tmp_path, monkeypatch, select_name):
    missing = tmp_path / "deleted-project"
    context = SimpleNamespace(
        user_data={
            "directory_browser_current": str(tmp_path),
            "directory_browser_git_projects": [str(missing)],
        },
        bot_data={"db": temp_db},
    )
    monkeypatch.setattr("subprocess.run", Mock(side_effect=FileNotFoundError))
    update = _query_update("dir:gitproj:0")

    state = await getattr(projects, select_name)(update, context)

    assert state == projects.ADDPROJ_BROWSE
    assert await SavedProjectRepository(temp_db).list_projects(123456789) == []
    assert "تم حذفه" in update.callback_query.edit_message_text.call_args.args[0]


@pytest.mark.asyncio
async def test_project_deleted_after_navigation_is_rejected_at_confirmation(temp_db, tmp_path):
    context = SimpleNamespace(
        user_data={"directory_browser_current": str(tmp_path / "deleted")},
        bot_data={"db": temp_db},
    )
    update = _query_update("dir:confirm")

    assert await projects.addproj_confirm_callback(update, context) == projects.ADDPROJ_BROWSE
    assert await SavedProjectRepository(temp_db).list_projects(123456789) == []
    assert "تم حذفه" in update.callback_query.edit_message_text.call_args.args[0]


@pytest.mark.asyncio
@pytest.mark.parametrize("error", [DirectoryError.NOT_FOUND, DirectoryError.PERMISSION_DENIED,
                                  DirectoryError.UNAVAILABLE])
async def test_browser_listing_error_skips_git_scan_and_keeps_navigation(tmp_path, monkeypatch, error):
    context = SimpleNamespace(user_data={
        "directory_browser_current": str(tmp_path),
        "directory_browser_git_projects": [str(tmp_path / "stale")],
    })
    monkeypatch.setattr(
        browser_ui.directory_browser, "list_directories",
        Mock(return_value=DirectoryListing(path=tmp_path, directories=[], error=error)),
    )
    scan = Mock(side_effect=AssertionError("must not scan an unavailable directory"))
    monkeypatch.setattr(browser_ui, "_scan_git_projects", scan)
    update = _query_update("dir:drive:0")

    await browser_ui._show_directory(update, context)

    scan.assert_not_called()
    text = update.callback_query.edit_message_text.call_args.args[0]
    assert browser_ui._error_message(error) in text
    assert context.user_data["directory_browser_git_projects"] == []
    markup = update.callback_query.edit_message_text.call_args.kwargs["reply_markup"]
    callbacks = [button.callback_data for row in markup.inline_keyboard for button in row]
    assert "dir:back" in callbacks and "dir:drives" in callbacks
    assert "dir:confirm" not in callbacks


@pytest.mark.parametrize("error", [FileNotFoundError, PermissionError, OSError])
def test_git_scan_handles_directory_disappearing_mid_scan(tmp_path, monkeypatch, error):
    def fail(_path):
        raise error("not accessible")

    monkeypatch.setattr(Path, "iterdir", fail)
    assert browser_ui._scan_git_projects(tmp_path) == []


@pytest.mark.asyncio
async def test_browser_replaces_drives_button_with_confirm_button_when_valid_directory(tmp_path):
    subfolder = tmp_path / "sub"
    subfolder.mkdir()
    context = SimpleNamespace(user_data={
        "directory_browser_current": str(tmp_path),
        "directory_browser_confirm_label": "تأكيد موقع المشروع",
    })
    update = _query_update("dir:drive:0")

    await browser_ui._show_directory(update, context)

    markup = update.callback_query.edit_message_text.call_args.kwargs["reply_markup"]
    callbacks = [button.callback_data for row in markup.inline_keyboard for button in row]
    labels = [button.text for row in markup.inline_keyboard for button in row]

    assert "dir:confirm" in callbacks
    assert "dir:drives" not in callbacks
    assert "تأكيد موقع المشروع" in labels

