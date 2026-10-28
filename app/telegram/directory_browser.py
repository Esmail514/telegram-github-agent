"""Reusable inline directory browser for approved local path selection."""
from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import ContextTypes

from app.runner.directory_browser import DirectoryError, directory_browser
from app.utils.security import sanitize_for_telegram

CB_DIRECTORY_DRIVE = "dir:drive:"
CB_DIRECTORY_FOLDER = "dir:folder:"
CB_DIRECTORY_PAGE = "dir:page:"
CB_DIRECTORY_BACK = "dir:back"
CB_DIRECTORY_PARENT = "dir:parent"
CB_DIRECTORY_DRIVES = "dir:drives"
CB_DIRECTORY_CONFIRM = "dir:confirm"
CB_DIRECTORY_GIT_PROJ = "dir:gitproj:"

DIRECTORY_NAVIGATION_PATTERN = r"^dir:(drive:\d+|folder:\d+|page:\d+|back|parent|drives)$"

_PAGE_SIZE = 8
_PREFIX = "directory_browser_"


def _scan_git_projects(path: Path) -> list[Path]:
    """Return immediate subdirectories (and the path itself) that contain a .git dir."""
    results: list[Path] = []
    try:
        if (path / ".git").exists():
            results.append(path)
        for child in sorted(path.iterdir()):
            if child.is_dir() and child != path and (child / ".git").exists():
                results.append(child)
    except OSError:
        pass
    return results


def current_directory(context: ContextTypes.DEFAULT_TYPE) -> Path | None:
    raw_path = _data(context).get(f"{_PREFIX}current")
    return Path(raw_path) if isinstance(raw_path, str) and raw_path else None


async def start_directory_browser(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    confirm_label: str,
) -> None:
    """Reset the browsing state and show real mounted drives."""
    data = _data(context)
    for key in list(data):
        if key.startswith(_PREFIX):
            data.pop(key, None)
    data[f"{_PREFIX}confirm_label"] = confirm_label
    await _show_drives(update, context)


async def navigate_directory_browser(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Apply a bounded callback navigation action and render the next browser view."""
    query = update.callback_query
    assert query
    await query.answer()
    data = _data(context)
    action = query.data or ""

    if action.startswith(CB_DIRECTORY_DRIVE):
        drives = _paths(data.get(f"{_PREFIX}drives"))
        selected = _indexed_path(drives, action.removeprefix(CB_DIRECTORY_DRIVE))
        if selected is None:
            await _show_drives(update, context, "لم يعد هذا الـ Partition متاحًا. اختر واحدًا آخر.")
            return
        data[f"{_PREFIX}current"] = str(selected)
        data[f"{_PREFIX}history"] = []
        data[f"{_PREFIX}page"] = 0
        await _show_directory(update, context)
        return

    if action.startswith((CB_DIRECTORY_FOLDER, CB_DIRECTORY_GIT_PROJ)):
        is_git_shortcut = action.startswith(CB_DIRECTORY_GIT_PROJ)
        prefix = CB_DIRECTORY_GIT_PROJ if is_git_shortcut else CB_DIRECTORY_FOLDER
        key = "git_projects" if is_git_shortcut else "folders"
        folders = _paths(data.get(f"{_PREFIX}{key}"))
        selected = _indexed_path(folders, action.removeprefix(prefix))
        current = current_directory(context)
        if selected is None or current is None:
            await _show_drives(update, context, "تعذر فتح المجلد. أعد اختيار المسار.")
            return
        history = list(data.get(f"{_PREFIX}history", []))
        if selected != current:
            history.append(str(current))
        data[f"{_PREFIX}history"] = history
        data[f"{_PREFIX}current"] = str(selected)
        data[f"{_PREFIX}page"] = 0
        await _show_directory(update, context)
        return

    if action.startswith(CB_DIRECTORY_PAGE):
        try:
            page = max(0, int(action.removeprefix(CB_DIRECTORY_PAGE)))
        except ValueError:
            page = 0
        data[f"{_PREFIX}page"] = page
        await _show_directory(update, context, refresh=False)
        return

    current = current_directory(context)
    if action == CB_DIRECTORY_BACK:
        history = list(data.get(f"{_PREFIX}history", []))
        if not history:
            await _show_drives(update, context)
            return
        data[f"{_PREFIX}current"] = history.pop()
        data[f"{_PREFIX}history"] = history
        data[f"{_PREFIX}page"] = 0
        await _show_directory(update, context)
        return

    if action == CB_DIRECTORY_PARENT:
        if current is None or current.parent == current:
            await _show_drives(update, context)
            return
        history = list(data.get(f"{_PREFIX}history", []))
        history.append(str(current))
        data[f"{_PREFIX}history"] = history
        data[f"{_PREFIX}current"] = str(current.parent)
        data[f"{_PREFIX}page"] = 0
        await _show_directory(update, context)
        return

    if action == CB_DIRECTORY_DRIVES:
        await _show_drives(update, context)


async def validate_current_directory(
    context: ContextTypes.DEFAULT_TYPE,
) -> tuple[Path | None, str | None]:
    path = current_directory(context)
    if path is None:
        return None, "اختر مجلدًا أولًا."
    resolved, error = await asyncio.to_thread(directory_browser.validate_directory, path)
    if resolved is not None:
        return resolved, None
    return None, _error_message(error)


async def show_browser_error(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    message: str,
) -> None:
    """Keep the current browser location on screen after failed confirmation."""
    if current_directory(context) is None:
        await _show_drives(update, context, message)
    else:
        await _show_directory(update, context, message=message)


async def _show_drives(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    message: str | None = None,
) -> None:
    data = _data(context)
    drives = await asyncio.to_thread(directory_browser.list_drives)
    data[f"{_PREFIX}drives"] = [str(path) for path in drives]
    data.pop(f"{_PREFIX}current", None)
    data[f"{_PREFIX}history"] = []
    data[f"{_PREFIX}page"] = 0
    rows = [
        [InlineKeyboardButton(_display_path(path), callback_data=f"{CB_DIRECTORY_DRIVE}{index}")]
        for index, path in enumerate(drives)
    ]
    text = "*اختيار موقع المشروع*\nاختر الـ Partition / Drive."
    if message:
        text += f"\n\n{message}"
    if not drives:
        text += "\n\nلا توجد Partitions متاحة يمكن الوصول إليها."
    await _show(update, text, InlineKeyboardMarkup(rows))


async def _show_directory(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    message: str | None = None,
    refresh: bool = True,
) -> None:
    data = _data(context)
    current = current_directory(context)
    if current is None:
        await _show_drives(update, context, message)
        return
    if refresh:
        listing = await asyncio.to_thread(directory_browser.list_directories, current)
        if listing.error is None:
            data[f"{_PREFIX}current"] = str(listing.path)
        data[f"{_PREFIX}folders"] = [str(path) for path in listing.directories]
        data[f"{_PREFIX}error"] = listing.error
        active_path = current_directory(context) or current
        git_projects = (
            await asyncio.to_thread(_scan_git_projects, active_path)
            if listing.error is None else []
        )
        data[f"{_PREFIX}git_projects"] = [str(p) for p in git_projects]

    folders = _paths(data.get(f"{_PREFIX}folders"))
    git_projects = _paths(data.get(f"{_PREFIX}git_projects"))
    error = data.get(f"{_PREFIX}error")
    page = _safe_page(data.get(f"{_PREFIX}page"), len(folders))
    data[f"{_PREFIX}page"] = page
    active_path = current_directory(context) or current
    text = f"*اختيار موقع المشروع*\nالمسار الحالي:\n`{_display_path(active_path)}`"
    if error:
        text += f"\n\n{_error_message(error)}"
    elif message:
        text += f"\n\n{message}"
    elif git_projects:
        text += "\n\n[Git] المشاريع المكتشفة في هذا المجلد:"
    elif not folders:
        text += "\n\nلا توجد مجلدات فرعية هنا. يمكنك تأكيد هذا المجلد أو الرجوع."
    else:
        text += "\n\nاختر مجلدًا للدخول إليه أو أكد هذا المجلد."

    rows: list[list[InlineKeyboardButton]] = []

    # Show git project buttons at the top
    for idx, proj_path in enumerate(git_projects):
        label = f"[git] {proj_path.name}"
        rows.append([InlineKeyboardButton(label, callback_data=f"{CB_DIRECTORY_GIT_PROJ}{idx}")])

    start = page * _PAGE_SIZE
    for index, folder in enumerate(folders[start : start + _PAGE_SIZE], start=start):
        rows.append([InlineKeyboardButton(folder.name[:56], callback_data=f"{CB_DIRECTORY_FOLDER}{index}")])
    navigation: list[InlineKeyboardButton] = []
    if page > 0:
        navigation.append(InlineKeyboardButton("السابق", callback_data=f"{CB_DIRECTORY_PAGE}{page - 1}"))
    if start + _PAGE_SIZE < len(folders):
        navigation.append(InlineKeyboardButton("التالي", callback_data=f"{CB_DIRECTORY_PAGE}{page + 1}"))
    if navigation:
        rows.append(navigation)
    rows.append(
        [
            InlineKeyboardButton("رجوع", callback_data=CB_DIRECTORY_BACK),
            InlineKeyboardButton("المجلد الأب", callback_data=CB_DIRECTORY_PARENT),
        ]
    )
    if error:
        rows.append([InlineKeyboardButton("اختيار Partition آخر", callback_data=CB_DIRECTORY_DRIVES)])
    else:
        confirm_label = str(data.get(f"{_PREFIX}confirm_label", "تأكيد موقع المشروع"))
        rows.append(
            [
                InlineKeyboardButton(
                    confirm_label,
                    callback_data=CB_DIRECTORY_CONFIRM,
                )
            ]
        )
    await _show(update, text, InlineKeyboardMarkup(rows))


async def _show(update: Update, text: str, keyboard: InlineKeyboardMarkup) -> None:
    text = sanitize_for_telegram(text)
    if update.callback_query:
        await update.callback_query.edit_message_text(text, reply_markup=keyboard, parse_mode="Markdown")
    elif update.message:
        await update.message.reply_text(text, reply_markup=keyboard, parse_mode="Markdown")


def _data(context: ContextTypes.DEFAULT_TYPE) -> dict[str, Any]:
    if context.user_data is None:
        raise RuntimeError("Directory browser state is unavailable")
    return context.user_data


def _paths(value: object) -> list[Path]:
    if not isinstance(value, list):
        return []
    return [Path(item) for item in value if isinstance(item, str)]


def _indexed_path(paths: list[Path], raw_index: str) -> Path | None:
    try:
        index = int(raw_index)
    except ValueError:
        return None
    return paths[index] if 0 <= index < len(paths) else None


def _safe_page(value: object, item_count: int) -> int:
    if not isinstance(value, (int, str)):
        return 0
    try:
        page = max(0, int(value))
    except (TypeError, ValueError):
        return 0
    return min(page, max(0, (item_count - 1) // _PAGE_SIZE))


def _display_path(path: Path) -> str:
    return str(path).replace("`", "'")


def _error_message(error: object) -> str:
    messages: dict[str, str] = {
        DirectoryError.NOT_FOUND: "المسار لم يعد موجودًا أو تم حذفه.",
        DirectoryError.NOT_DIRECTORY: "المسار المحدد ليس مجلدًا.",
        DirectoryError.PERMISSION_DENIED: "لا توجد صلاحية للوصول إلى هذا المجلد.",
        DirectoryError.UNAVAILABLE: "المجلد أو الـ Partition غير متاح حاليًا.",
    }
    if not isinstance(error, str):
        return "تعذر الوصول إلى هذا المجلد."
    return messages.get(error, "تعذر الوصول إلى هذا المجلد.")
