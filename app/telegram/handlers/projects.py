"""
/projects and /setdir handlers — browse local projects and configure the projects root directory.
"""
from __future__ import annotations

import logging
from pathlib import Path

from telegram import Update
from telegram.ext import (
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    ConversationHandler,
    MessageHandler,
    filters,
)

from app.config.settings import settings
from app.runner.project_scanner import LocalProject, project_scanner
from app.telegram.auth import auth_required
from app.telegram.keyboards import (
    CB_CANCEL,
    CB_PROJECT,
    CB_PROJECT_PAGE,
    CB_REFRESH_PROJ,
    CB_SETDIR,
    cancel_keyboard,
    project_detail_keyboard,
    projects_keyboard,
)

logger = logging.getLogger(__name__)

# Conversation states for /setdir
SETDIR_ENTER_PATH = 1


# ---------------------------------------------------------------------------
# Project Browsing
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
    """Scan PROJECTS_DIR and display paginated projects."""
    projects_dir = settings.PROJECTS_DIR

    if not projects_dir or not projects_dir.exists():
        msg = (
            "📂 *Projects Directory Not Set*\n\n"
            "Please configure the directory on this computer where your projects reside.\n\n"
            "• Use command: `/setdir <path>`\n"
            "• Or click the button below to type it."
        )
        from telegram import InlineKeyboardButton, InlineKeyboardMarkup
        kb = InlineKeyboardMarkup([
            [InlineKeyboardButton("⚙️ Set Directory", callback_data=CB_SETDIR)],
            [InlineKeyboardButton("❌ Cancel", callback_data=CB_CANCEL)],
        ])
        if update.callback_query:
            await update.callback_query.edit_message_text(msg, reply_markup=kb, parse_mode="Markdown")
        elif update.message:
            await update.message.reply_text(msg, reply_markup=kb, parse_mode="Markdown")
        return

    projects = project_scanner.scan(projects_dir)
    if context.user_data is not None:
        context.user_data["scanned_projects"] = projects

    if not projects:
        msg = (
            f"📂 *No projects found* in:\n`{projects_dir}`\n\n"
            "Make sure your projects are folders inside this directory."
        )
        from telegram import InlineKeyboardButton, InlineKeyboardMarkup
        kb = InlineKeyboardMarkup([
            [InlineKeyboardButton("⚙️ Change Directory", callback_data=CB_SETDIR)],
            [InlineKeyboardButton("🔄 Refresh", callback_data=CB_REFRESH_PROJ)],
        ])
        if update.callback_query:
            await update.callback_query.edit_message_text(msg, reply_markup=kb, parse_mode="Markdown")
        elif update.message:
            await update.message.reply_text(msg, reply_markup=kb, parse_mode="Markdown")
        return

    text = (
        f"📁 *Local Projects* (page {page + 1})\n"
        f"📍 `{projects_dir}`\n\n"
        f"Select a project to inspect, view issues, or run an agent:"
    )
    keyboard = projects_keyboard(projects, page=page, include_cancel=True)

    if update.callback_query:
        await update.callback_query.edit_message_text(text, reply_markup=keyboard, parse_mode="Markdown")
    elif update.message:
        await update.message.reply_text(text, reply_markup=keyboard, parse_mode="Markdown")


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
    await query.answer("Refreshing projects...")
    await show_projects(update, context, page=0)


@auth_required
async def project_selected_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """User tapped a project in the list."""
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
        await query.edit_message_text("❌ Project not found. Refreshing...", reply_markup=None)
        await show_projects(update, context, page=0)
        return

    # Cache selection into user_data
    if context.user_data is not None:
        context.user_data["selected_project_name"] = project.name
        context.user_data["selected_project_path"] = str(project.path)
        context.user_data["selected_repo"] = project.repo_full_name
        context.user_data["run_repo"] = project.repo_full_name
        context.user_data["run_local_path"] = str(project.path)

    git_badge = "✅ Git Repository" if project.is_git else "⚠️ Not a Git Repo"
    github_line = (
        f"*GitHub:* `{project.repo_full_name}`"
        if project.repo_full_name
        else "*GitHub:* _(no remote origin linked)_"
    )
    branch_line = f"*Branch:* `{project.branch}`" if project.branch else ""

    text = (
        f"📁 *Project:* `{project.name}`\n\n"
        f"📍 *Path:* `{project.path}`\n"
        f"{github_line}\n"
        f"{branch_line}\n"
        f"⚙️ {git_badge}\n\n"
        f"Choose an action:"
    )

    await query.edit_message_text(
        text,
        reply_markup=project_detail_keyboard(idx, project.has_github_remote, project.repo_full_name),
        parse_mode="Markdown",
    )


# ---------------------------------------------------------------------------
# Set Projects Directory (/setdir)
# ---------------------------------------------------------------------------

@auth_required
async def setdir_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Direct command: /setdir [path]"""
    assert update.message

    # Check if path provided directly in arguments: /setdir D:\Work
    args = context.args
    if args:
        raw_path = " ".join(args).strip().strip('"').strip("'")
        return await _apply_new_projects_dir(update, context, raw_path)

    # Otherwise prompt user for path
    await update.message.reply_text(
        "📁 *Set Projects Directory*\n\n"
        "Please send the **absolute path** to your projects directory.\n\n"
        "*Examples:*\n"
        "• Windows: `D:\\Work`\n"
        "• Linux: `/home/user/projects`\n"
        "• macOS: `/Users/user/projects`\n\n"
        "Send /cancel to abort.",
        parse_mode="Markdown",
        reply_markup=cancel_keyboard(),
    )
    return SETDIR_ENTER_PATH


@auth_required
async def setdir_button_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Triggered when tapping [⚙️ Set Directory] button."""
    query = update.callback_query
    assert query
    await query.answer()

    await query.edit_message_text(
        "📁 *Set Projects Directory*\n\n"
        "Please type and send the **absolute path** to your projects directory.\n\n"
        "*Examples:*\n"
        "• Windows: `D:\\Work`\n"
        "• Linux: `/home/user/projects`\n\n"
        "Send /cancel to abort.",
        parse_mode="Markdown",
        reply_markup=cancel_keyboard(),
    )
    return SETDIR_ENTER_PATH


async def setdir_path_received(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Handle text input for new projects directory."""
    assert update.message and update.message.text
    raw_path = update.message.text.strip().strip('"').strip("'")
    return await _apply_new_projects_dir(update, context, raw_path)


async def _apply_new_projects_dir(
    update: Update, context: ContextTypes.DEFAULT_TYPE, raw_path: str
) -> int:
    target = Path(raw_path)

    if not target.exists():
        msg = (
            f"⚠️ *Path not found:*\n`{target}`\n\n"
            f"Please check the path and try again, or send /cancel."
        )
        if update.message:
            await update.message.reply_text(msg, parse_mode="Markdown", reply_markup=cancel_keyboard())
        elif update.callback_query:
            await update.callback_query.edit_message_text(msg, parse_mode="Markdown", reply_markup=cancel_keyboard())
        return SETDIR_ENTER_PATH

    if not target.is_dir():
        msg = f"⚠️ Path is a file, not a folder: `{target}`\n\nPlease send a folder path."
        if update.message:
            await update.message.reply_text(msg, parse_mode="Markdown", reply_markup=cancel_keyboard())
        elif update.callback_query:
            await update.callback_query.edit_message_text(msg, parse_mode="Markdown", reply_markup=cancel_keyboard())
        return SETDIR_ENTER_PATH

    # Valid directory — apply it
    settings.set_projects_dir(target)
    projects = project_scanner.scan(target)
    if context.user_data is not None:
        context.user_data["scanned_projects"] = projects

    git_count = sum(1 for p in projects if p.is_git)
    confirm_text = (
        f"✅ *Projects Directory Updated!*\n\n"
        f"📍 `{target.resolve()}`\n\n"
        f"Found **{len(projects)}** projects ({git_count} Git repositories)."
    )

    if update.message:
        await update.message.reply_text(confirm_text, parse_mode="Markdown")
        await show_projects(update, context, page=0)
    elif update.callback_query:
        await update.callback_query.edit_message_text(confirm_text, parse_mode="Markdown")

    return ConversationHandler.END


async def setdir_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    msg = "❌ Directory setup cancelled."
    if update.callback_query:
        await update.callback_query.answer()
        await update.callback_query.edit_message_text(msg)
    elif update.message:
        await update.message.reply_text(msg)
    return ConversationHandler.END


def build_setdir_handler() -> ConversationHandler:
    return ConversationHandler(
        entry_points=[
            CommandHandler("setdir", setdir_command),
            CallbackQueryHandler(setdir_button_callback, pattern=f"^{CB_SETDIR}$"),
        ],
        states={
            SETDIR_ENTER_PATH: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, setdir_path_received),
                CommandHandler("cancel", setdir_cancel),
            ],
        },
        fallbacks=[
            CommandHandler("cancel", setdir_cancel),
            CallbackQueryHandler(setdir_cancel, pattern=f"^{CB_CANCEL}$"),
        ],
        per_user=True,
        per_chat=True,
        per_message=False,
    )
