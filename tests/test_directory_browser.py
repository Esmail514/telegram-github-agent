from pathlib import Path
from types import SimpleNamespace

import pytest

from app.runner.directory_browser import DirectoryBrowser, DirectoryError


def test_directory_browser_lists_configured_drives_and_nested_folders(tmp_path: Path):
    drive = tmp_path / "D"
    project = drive / "Projects" / "Python" / "my_project"
    project.mkdir(parents=True)
    (drive / "README.txt").write_text("not a folder", encoding="utf-8")

    browser = DirectoryBrowser(drive_provider=lambda: [drive])

    assert browser.list_drives() == [drive.resolve()]
    root = browser.list_directories(drive)
    assert root.error is None
    assert [path.name for path in root.directories] == ["Projects"]

    projects = browser.list_directories(root.directories[0])
    assert [path.name for path in projects.directories] == ["Python"]

    python_dir = browser.list_directories(projects.directories[0])
    assert [path.name for path in python_dir.directories] == ["my_project"]
    assert browser.validate_directory(python_dir.directories[0]) == (project.resolve(), None)


def test_directory_browser_reports_missing_directory(tmp_path: Path):
    browser = DirectoryBrowser(drive_provider=lambda: [tmp_path])

    result = browser.list_directories(tmp_path / "deleted")

    assert result.error == DirectoryError.NOT_FOUND
    assert browser.validate_directory(tmp_path / "deleted") == (None, DirectoryError.NOT_FOUND)


def test_directory_browser_reports_permission_denied(tmp_path: Path, monkeypatch):
    browser = DirectoryBrowser(drive_provider=lambda: [tmp_path])

    def denied(_self):
        raise PermissionError

    monkeypatch.setattr(Path, "iterdir", denied)
    result = browser.list_directories(tmp_path)

    assert result.error == DirectoryError.PERMISSION_DENIED


def test_directory_browser_reports_unavailable_partition(tmp_path: Path, monkeypatch):
    browser = DirectoryBrowser(drive_provider=lambda: [tmp_path])

    def unavailable(_self, strict=False):
        raise OSError("drive is unavailable")

    monkeypatch.setattr(Path, "resolve", unavailable)

    assert browser.list_directories(tmp_path).error == DirectoryError.UNAVAILABLE
    assert browser.list_drives() == []


class _Message:
    def __init__(self) -> None:
        self.sent: list[tuple[str, object]] = []

    async def reply_text(self, text: str, reply_markup=None, parse_mode=None) -> None:
        self.sent.append((text, reply_markup))


class _Query:
    def __init__(self, data: str) -> None:
        self.data = data
        self.edited: list[tuple[str, object]] = []

    async def answer(self) -> None:
        return None

    async def edit_message_text(self, text: str, reply_markup=None, parse_mode=None) -> None:
        self.edited.append((text, reply_markup))


def _context() -> SimpleNamespace:
    return SimpleNamespace(user_data={})


@pytest.mark.asyncio
async def test_inline_browser_navigates_drive_folder_parent_back_and_drives(tmp_path: Path, monkeypatch):
    import app.telegram.directory_browser as browser_ui

    drive = tmp_path / "D"
    child = drive / "Projects"
    child.mkdir(parents=True)
    monkeypatch.setattr(browser_ui, "directory_browser", DirectoryBrowser(lambda: [drive]))
    context = _context()
    message = _Message()

    await browser_ui.start_directory_browser(
        SimpleNamespace(callback_query=None, message=message), context, "Confirm Project Location"
    )
    assert "Partition" in message.sent[-1][0]

    drive_query = _Query("dir:drive:0")
    await browser_ui.navigate_directory_browser(
        SimpleNamespace(callback_query=drive_query, message=None), context
    )
    assert browser_ui.current_directory(context) == drive.resolve()
    assert str(drive.resolve()) in drive_query.edited[-1][0]

    folder_query = _Query("dir:folder:0")
    await browser_ui.navigate_directory_browser(
        SimpleNamespace(callback_query=folder_query, message=None), context
    )
    assert browser_ui.current_directory(context) == child.resolve()
    assert await browser_ui.validate_current_directory(context) == (child.resolve(), None)

    parent_query = _Query("dir:parent")
    await browser_ui.navigate_directory_browser(
        SimpleNamespace(callback_query=parent_query, message=None), context
    )
    assert browser_ui.current_directory(context) == drive.resolve()

    back_query = _Query("dir:back")
    await browser_ui.navigate_directory_browser(
        SimpleNamespace(callback_query=back_query, message=None), context
    )
    assert browser_ui.current_directory(context) == child.resolve()

    drives_query = _Query("dir:drives")
    await browser_ui.navigate_directory_browser(
        SimpleNamespace(callback_query=drives_query, message=None), context
    )
    assert browser_ui.current_directory(context) is None


@pytest.mark.asyncio
async def test_confirming_projects_directory_saves_only_the_confirmed_path(temp_db, tmp_path: Path):
    """setdir_confirm now saves a git project to saved_projects (not ProjectRoot)."""
    from app.database.repository import SavedProjectRepository
    from app.telegram.handlers.projects import setdir_confirm_callback

    # Create a git repo so the new handler accepts it
    root = tmp_path / "my_git_project"
    root.mkdir()
    (root / ".git").mkdir()

    context = SimpleNamespace(
        user_data={"directory_browser_current": str(root)},
        bot_data={"db": temp_db},
    )
    query = _Query("dir:confirm")
    update = SimpleNamespace(
        callback_query=query,
        message=None,
        effective_user=SimpleNamespace(id=123456789, username=None),
    )

    projects_before = await SavedProjectRepository(temp_db).list_projects(123456789)
    assert len(projects_before) == 0

    await setdir_confirm_callback(update, context)

    projects_after = await SavedProjectRepository(temp_db).list_projects(123456789)
    assert len(projects_after) == 1
    assert projects_after[0].path == str(root.resolve())
    assert projects_after[0].name == root.name




@pytest.mark.asyncio
async def test_confirming_local_agent_folder_saves_the_selected_project_path(temp_db, tmp_path: Path):
    from app.database.repository import SelectedProjectRepository
    from app.telegram.handlers.run import RUN_CONFIRM, run_local_browser_confirm

    project = tmp_path / "my_project"
    (project / ".git").mkdir(parents=True)
    context = SimpleNamespace(
        user_data={
            "directory_browser_current": str(project),
            "run_repo": "owner/my_project",
            "run_issue": 7,
        },
        bot_data={"db": temp_db},
    )
    query = _Query("dir:confirm")
    update = SimpleNamespace(
        callback_query=query,
        message=None,
        effective_user=SimpleNamespace(id=123456789, username=None),
    )

    state = await run_local_browser_confirm(update, context)

    selected = await SelectedProjectRepository(temp_db).get(123456789)
    assert state == RUN_CONFIRM
    assert selected is not None
    assert selected.name == "my_project"
    assert selected.path == str(project.resolve())
