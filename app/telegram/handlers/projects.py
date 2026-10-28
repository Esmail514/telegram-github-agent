"""
/projects and /setdir handlers — browse saved projects and configure individual git project paths.

New flow (multi-project):
  - show_projects: loads saved_projects from DB and shows a paginated list
  - add project: opens directory browser; git repos appear as selectable items
  - saved_project_selected: shows a saved project's details with run/delete options
  - saved_proj_delete: asks confirmation before deletion
  - saved_proj_delete_confirm: actually removes from DB
"""
from __future__ import annotations

import logging
import re
from pathlib import Path

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import (
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    ConversationHandler,
    MessageHandler,
    filters,
)

from app.runner.project_scanner import LocalProject, project_scanner
from app.telegram.auth import auth_required
from app.telegram.directory_browser import (
    _PREFIX as _DIR_PREFIX,
)
from app.telegram.directory_browser import (
    CB_DIRECTORY_CONFIRM,
    DIRECTORY_NAVIGATION_PATTERN,
    navigate_directory_browser,
    show_browser_error,
    start_directory_browser,
    validate_current_directory,
)
from app.telegram.keyboards import (
    CB_ADD_PROJ,
    CB_PROJECT,
    CB_PROJECT_PAGE,
    CB_SAVED_PROJ,
    CB_SAVED_PROJ_DEL,
    CB_SAVED_PROJ_DEL_CONFIRM,
    CB_SAVED_PROJ_PAGE,
    CB_SETDIR,
    MAIN_MENU_GITHUB,
    MAIN_MENU_PERSONAL,
    MAIN_MENU_PROJECTS,
    MAIN_MENU_SCHEDULES,
    MAIN_MENU_STATUS,
    MAIN_MENU_SYSTEM,
    project_detail_keyboard,
    saved_project_delete_confirm_keyboard,
    saved_project_detail_keyboard,
    saved_projects_keyboard,
)
from app.utils.security import sanitize_for_telegram

logger = logging.getLogger(__name__)

# Conversation states
SETDIR_BROWSE = 1
ADDPROJ_BROWSE = 1  # same numeric value, different ConversationHandler
_MENU_TEXT = filters.Regex(
    r"^(" + "|".join(re.escape(label) for label in (
        MAIN_MENU_GITHUB, MAIN_MENU_PERSONAL, MAIN_MENU_PROJECTS,
        MAIN_MENU_SCHEDULES, MAIN_MENU_STATUS, MAIN_MENU_SYSTEM,
    )) + r")$"
)


# ---------------------------------------------------------------------------
# Saved Projects — list (main entry)
# ---------------------------------------------------------------------------


@auth_required
async def projects_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Entry point for /projects command."""
    await show_projects(update, context, page=0)


async def show_projects(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    page: int = 0,
) -> None:
    """Load saved projects from DB and display a paginated list."""
    db = context.bot_data.get("db")
    user = update.effective_user

    if db is None or user is None:
        _no_db_message = "️ قاعدة البيانات غير متاحة."
        if update.callback_query:
            await update.callback_query.edit_message_text(_no_db_message)
        elif update.message:
            await update.message.reply_text(_no_db_message)
        return

    from app.database.repository import SavedProjectRepository

    repo = SavedProjectRepository(db)
    projects = await repo.list_projects(user.id)

    if not projects:
        msg = (
            " *لا توجد مشاريع محفوظة بعد*\n\n"
            "اضغط على الزر أدناه لإضافة مشروع جديد.\n"
            "يمكنك إضافة أكثر من مشروع وتبديل بينها عند الحاجة."
        )
        kb = InlineKeyboardMarkup([
            [InlineKeyboardButton(" إضافة مشروع", callback_data=CB_ADD_PROJ)],
        ])
        if update.callback_query:
            await update.callback_query.edit_message_text(msg, reply_markup=kb, parse_mode="Markdown")
        elif update.message:
            await update.message.reply_text(msg, reply_markup=kb, parse_mode="Markdown")
        return

    text = (
        f"📁 *المشاريع المحفوظة* — {len(projects)} مشروع\n\n"
        "اختر مشروعًا للعمل عليه، أو أضف مشروعًا جديدًا."
    )
    keyboard = saved_projects_keyboard(projects, page=page)

    if update.callback_query:
        await update.callback_query.edit_message_text(text, reply_markup=keyboard, parse_mode="Markdown")
    elif update.message:
        await update.message.reply_text(text, reply_markup=keyboard, parse_mode="Markdown")


@auth_required
async def saved_proj_page_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Paginate through saved projects."""
    query = update.callback_query
    assert query
    await query.answer()
    try:
        page = int((query.data or "").replace(CB_SAVED_PROJ_PAGE, ""))
    except ValueError:
        page = 0
    await show_projects(update, context, page=page)


# ---------------------------------------------------------------------------
# Saved Project — detail / select
# ---------------------------------------------------------------------------


@auth_required
async def saved_proj_selected_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """User tapped a saved project  show detail with run/delete options."""
    query = update.callback_query
    assert query
    await query.answer()

    db = context.bot_data.get("db")
    if db is None:
        await query.answer("قاعدة البيانات غير متاحة", show_alert=True)
        return

    from app.database.repository import SavedProjectRepository, SelectedProjectRepository

    raw_id = (query.data or "").replace(CB_SAVED_PROJ, "")
    try:
        project_id = int(raw_id)
    except ValueError:
        await show_projects(update, context, page=0)
        return

    proj = await SavedProjectRepository(db).get_by_id(project_id)
    user = update.effective_user
    if proj is None or user is None or proj.user_id != user.id:
        await query.answer("المشروع غير متاح.", show_alert=True)
        await show_projects(update, context, page=0)
        return

    # Set as the active selected project
    await SelectedProjectRepository(db).save(
        user.id, proj.name, proj.path, proj.repo_full_name
    )

    # Cache in user_data for the run handler
    if context.user_data is not None:
        context.user_data["selected_project_name"] = proj.name
        context.user_data["selected_project_path"] = proj.path
        context.user_data["selected_repo"] = proj.repo_full_name
        context.user_data["run_repo"] = proj.repo_full_name
        context.user_data["run_local_path"] = proj.path

    has_github = bool(proj.repo_full_name)
    github_line = (
        f"*GitHub:* `{proj.repo_full_name}`"
        if proj.repo_full_name
        else "*GitHub:* _(غير مرتبط بمستودع على الإنترنت)_"
    )
    path_obj = Path(proj.path)
    is_git = (path_obj / ".git").is_dir()
    git_badge = " مستودع Git" if is_git else " ليس مستودع Git"

    text = sanitize_for_telegram(
        f"* {proj.name}*\n\n"
        f" *المسار:* `{proj.path}`\n"
        f"{github_line}\n"
        f"{git_badge}\n\n"
        "اختر الإجراء المطلوب:"
    )
    await query.edit_message_text(
        text,
        reply_markup=saved_project_detail_keyboard(project_id, has_github, proj.repo_full_name),
        parse_mode="Markdown",
    )


# ---------------------------------------------------------------------------
# Saved Project — delete flow
# ---------------------------------------------------------------------------


@auth_required
async def saved_proj_delete_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Ask for confirmation before deleting a project."""
    query = update.callback_query
    assert query
    await query.answer()

    raw_id = (query.data or "").replace(CB_SAVED_PROJ_DEL, "")
    try:
        project_id = int(raw_id)
    except ValueError:
        await show_projects(update, context, page=0)
        return

    db = context.bot_data.get("db")
    if db is None:
        await query.answer("قاعدة البيانات غير متاحة", show_alert=True)
        return

    from app.database.repository import SavedProjectRepository

    proj = await SavedProjectRepository(db).get_by_id(project_id)
    user = update.effective_user
    if proj is None or user is None or proj.user_id != user.id:
        await query.answer("المشروع غير متاح.", show_alert=True)
        await show_projects(update, context, page=0)
        return

    await query.edit_message_text(
        sanitize_for_telegram(
            f"️ *تأكيد حذف المشروع*\n\n"
            f"هل تريد حذف المشروع *{proj.name}*?\n"
            f"المسار: `{proj.path}`\n\n"
            "هذا الإجراء لا يمكن التراجع عنه."
        ),
        reply_markup=saved_project_delete_confirm_keyboard(project_id),
        parse_mode="Markdown",
    )


@auth_required
async def saved_proj_delete_confirm_callback(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    """Actually delete the project after confirmation."""
    query = update.callback_query
    assert query
    await query.answer()

    raw_id = (query.data or "").replace(CB_SAVED_PROJ_DEL_CONFIRM, "")
    try:
        project_id = int(raw_id)
    except ValueError:
        await show_projects(update, context, page=0)
        return

    db = context.bot_data.get("db")
    if db is None:
        await query.answer("قاعدة البيانات غير متاحة", show_alert=True)
        return

    from app.database.repository import SavedProjectRepository

    proj = await SavedProjectRepository(db).get_by_id(project_id)
    user = update.effective_user
    if proj is None or user is None or proj.user_id != user.id:
        await query.answer("المشروع غير متاح.", show_alert=True)
        await show_projects(update, context, page=0)
        return
    name = proj.name
    await SavedProjectRepository(db).delete(project_id)
    await query.answer(f" تم حذف '{name}' بنجاح")
    await show_projects(update, context, page=0)


# ---------------------------------------------------------------------------
# Add Project — directory browser conversation
# ---------------------------------------------------------------------------


@auth_required
async def add_proj_button_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Entry: user pressed 'إضافة مشروع'  open directory browser."""
    query = update.callback_query
    assert query
    await query.answer()
    await start_directory_browser(update, context, "تأكيد موقع المشروع")
    return ADDPROJ_BROWSE


@auth_required
async def addproj_browser_navigate(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Navigate the directory browser while adding a project."""
    await navigate_directory_browser(update, context)
    return ADDPROJ_BROWSE


@auth_required
async def addproj_git_proj_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Navigate to a Git project and wait for explicit confirmation."""
    await navigate_directory_browser(update, context)
    return ADDPROJ_BROWSE


@auth_required
async def addproj_confirm_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """User pressed 'تأكيد' on a folder that is itself a git repo  save."""
    query = update.callback_query
    assert query
    await query.answer()

    target, error = await validate_current_directory(context)
    if target is None:
        await show_browser_error(update, context, error or "تعذر حفظ المسار.")
        return ADDPROJ_BROWSE

    git_dir = target / ".git"
    if not git_dir.exists():
        await show_browser_error(
            update, context,
            " هذا المجلد لا يحتوي على مستودع Git.\n"
            "اختر مشروعًا من القائمة أعلاه أو تصفح إلى مجلد آخر."
        )
        return ADDPROJ_BROWSE

    return await _save_git_project(update, context, target)


async def _save_git_project(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    proj_path: Path,
) -> int:
    """Persist a discovered git project to saved_projects and show confirmation."""
    query = update.callback_query
    db = context.bot_data.get("db")
    user = update.effective_user

    if db is None or user is None:
        if query:
            await query.edit_message_text("️ قاعدة البيانات غير متاحة.")
        return ConversationHandler.END

    # Detect github remote name from git config
    repo_full_name: str | None = None
    try:
        import subprocess
        result = subprocess.run(
            ["git", "remote", "get-url", "origin"],
            capture_output=True, text=True, cwd=str(proj_path), timeout=5,
        )
        if result.returncode == 0:
            url = result.stdout.strip()
            # Parse github.com/owner/repo(.git)
            import re
            match = re.search(r"github\.com[:/](.+?/[^/]+?)(?:\.git)?$", url)
            if match:
                repo_full_name = match.group(1)
    except Exception:
        pass

    project_name = proj_path.name

    from app.database.repository import SavedProjectRepository

    proj = await SavedProjectRepository(db).add(
        user.id, project_name, str(proj_path), repo_full_name
    )

    github_line = (
        f"*GitHub:* `{repo_full_name}`"
        if repo_full_name
        else "*GitHub:* _(لا يوجد remote مرتبط بـ GitHub)_"
    )
    text = sanitize_for_telegram(
        f" *تم حفظ المشروع بنجاح*\n\n"
        f"*الاسم:* `{proj.name}`\n"
        f"*المسار:* `{proj.path}`\n"
        f"{github_line}\n\n"
        "يمكنك إضافة مزيد من المشاريع أو العودة للقائمة."
    )
    if query:
        await query.edit_message_text(
            text,
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton(" عرض المشاريع", callback_data="menu:projects")],
                [InlineKeyboardButton(" إضافة مشروع آخر", callback_data=CB_ADD_PROJ)],
            ]),
            parse_mode="Markdown",
        )
    return ConversationHandler.END


async def addproj_to_menu(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Cancel add-project conversation and return to main menu."""
    if update.callback_query:
        await update.callback_query.answer()
        await update.callback_query.edit_message_text("⬅️ تم الرجوع.")
    return ConversationHandler.END


@auth_required
async def project_browser_main_menu(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    from app.telegram.handlers.start import main_menu_message_handler

    if context.user_data is not None:
        for key in list(context.user_data):
            if key.startswith(_DIR_PREFIX):
                context.user_data.pop(key, None)
    await main_menu_message_handler(update, context)
    return ConversationHandler.END


def build_addproj_handler() -> ConversationHandler:
    """ConversationHandler for the add-project browser flow."""
    return ConversationHandler(
        entry_points=[
            CallbackQueryHandler(add_proj_button_callback, pattern=f"^{CB_ADD_PROJ}$"),
        ],
        states={
            ADDPROJ_BROWSE: [
                CallbackQueryHandler(addproj_browser_navigate, pattern=DIRECTORY_NAVIGATION_PATTERN),
                CallbackQueryHandler(addproj_confirm_callback, pattern=f"^{CB_DIRECTORY_CONFIRM}$"),
                CallbackQueryHandler(
                    addproj_git_proj_callback, pattern=r"^dir:gitproj:\d+$"
                ),
            ],
        },
        fallbacks=[
            MessageHandler(_MENU_TEXT, project_browser_main_menu),
            CallbackQueryHandler(addproj_to_menu, pattern="^menu:start$"),
        ],
        per_user=True,
        per_chat=True,
        per_message=False,
    )


# ---------------------------------------------------------------------------
# Legacy /setdir — kept for backward compat (e.g. /setdir command)
# ---------------------------------------------------------------------------


@auth_required
async def project_page_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    assert query
    await query.answer()
    page = int((query.data or "").replace(CB_PROJECT_PAGE, ""))
    await show_projects(update, context, page=page)


@auth_required
async def project_refresh_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    assert query
    await query.answer("جاري تحديث المشاريع...")
    await show_projects(update, context, page=0)


@auth_required
async def project_selected_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Legacy handler for old proj: callbacks (scanned project list)."""
    query = update.callback_query
    assert query
    await query.answer()

    idx_str = (query.data or "").replace(CB_PROJECT, "")
    projects: list[LocalProject] = (context.user_data or {}).get("scanned_projects") or []
    if not projects:
        projects = project_scanner.scan()
        if context.user_data is not None:
            context.user_data["scanned_projects"] = projects

    try:
        idx = int(idx_str)
        project = projects[idx]
    except (ValueError, IndexError):
        await query.edit_message_text("️ لم يتم العثور على المشروع، جاري التحديث...", reply_markup=None)
        await show_projects(update, context, page=0)
        return

    if context.user_data is not None:
        context.user_data["selected_project_name"] = project.name
        context.user_data["selected_project_path"] = str(project.path)
        context.user_data["selected_repo"] = project.repo_full_name
        context.user_data["run_repo"] = project.repo_full_name
        context.user_data["run_local_path"] = str(project.path)

    db = context.bot_data.get("db")
    if db is not None and update.effective_user is not None:
        from app.database.repository import SelectedProjectRepository

        await SelectedProjectRepository(db).save(
            update.effective_user.id,
            project.name,
            str(project.path),
            project.repo_full_name,
        )

    git_badge = " مستودع Git" if project.is_git else " ليس مستودع Git"
    github_line = (
        f"*GitHub:* `{project.repo_full_name}`"
        if project.repo_full_name
        else "*GitHub:* _(غير مرتبط)_"
    )
    branch_line = f"*الفرع:* `{project.branch}`" if project.branch else ""

    text = sanitize_for_telegram(
        f"*تم حفظ المشروع:* `{project.name}`\n\n"
        f" *المسار:* `{project.path}`\n"
        f"{github_line}\n"
        f"{branch_line}\n"
        f"{git_badge}\n\n"
        "اختر الإجراء المطلوب:"
    )

    await query.edit_message_text(
        text,
        reply_markup=project_detail_keyboard(idx, project.has_github_remote, project.repo_full_name),
        parse_mode="Markdown",
    )


@auth_required
async def setdir_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Legacy /setdir command — now opens the add-project browser."""
    assert update.message
    await start_directory_browser(update, context, "تأكيد موقع المشروع")
    return SETDIR_BROWSE


@auth_required
async def setdir_button_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    assert query
    await query.answer()
    await start_directory_browser(update, context, "تأكيد موقع المشروع")
    return SETDIR_BROWSE


@auth_required
async def setdir_browser_navigate(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    await navigate_directory_browser(update, context)
    return SETDIR_BROWSE


@auth_required
async def setdir_confirm_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Confirm in setdir legacy flow (also triggered when current dir is a git repo)."""
    query = update.callback_query
    assert query
    await query.answer()
    target, error = await validate_current_directory(context)
    if target is None:
        await show_browser_error(update, context, error or "تعذر حفظ المسار.")
        return SETDIR_BROWSE

    if not (target / ".git").exists():
        await show_browser_error(
            update, context,
            " هذا المجلد لا يحتوي على مستودع Git.\n"
            "اختر مشروعًا من القائمة أعلاه."
        )
        return SETDIR_BROWSE

    return await _save_git_project(update, context, target)


@auth_required
async def setdir_git_proj_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Handle dir:gitproj: in the legacy /setdir conversation."""
    await navigate_directory_browser(update, context)
    return SETDIR_BROWSE


async def setdir_to_menu(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    if update.callback_query:
        await update.callback_query.answer()
        await update.callback_query.edit_message_text("⬅️ تم الرجوع.")
    return ConversationHandler.END


def build_setdir_handler() -> ConversationHandler:
    return ConversationHandler(
        entry_points=[
            CommandHandler("setdir", setdir_command),
            CallbackQueryHandler(setdir_button_callback, pattern=f"^{CB_SETDIR}$"),
        ],
        states={
            SETDIR_BROWSE: [
                CallbackQueryHandler(setdir_browser_navigate, pattern=DIRECTORY_NAVIGATION_PATTERN),
                CallbackQueryHandler(setdir_confirm_callback, pattern=f"^{CB_DIRECTORY_CONFIRM}$"),
                CallbackQueryHandler(setdir_git_proj_callback, pattern=r"^dir:gitproj:\d+$"),
            ],
        },
        fallbacks=[
            MessageHandler(_MENU_TEXT, project_browser_main_menu),
            CallbackQueryHandler(setdir_to_menu, pattern="^menu:start$"),
        ],
        per_user=True,
        per_chat=True,
        per_message=False,
    )
