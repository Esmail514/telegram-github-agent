"""
/run handler — launch an AI agent job for a selected issue.

Flow:
    Select Repo  Select Issue  [Clone / Local Path]  Browse folders  Confirm Agent  Launch
"""
from __future__ import annotations

import asyncio
import logging
import re
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

from app.github.client import github_client
from app.github.issues import issue_service
from app.runner.directory_browser import directory_browser
from app.runner.jobs import make_branch_name
from app.runner.project_scanner import project_scanner
from app.telegram.auth import auth_required
from app.telegram.callback_payloads import decode_callback_payload
from app.telegram.directory_browser import (
    CB_DIRECTORY_CONFIRM,
    DIRECTORY_NAVIGATION_PATTERN,
    navigate_directory_browser,
    show_browser_error,
    start_directory_browser,
    validate_current_directory,
)
from app.telegram.keyboards import (
    CB_ISSUE,
    MAIN_MENU_GITHUB,
    MAIN_MENU_PERSONAL,
    MAIN_MENU_PROJECTS,
    MAIN_MENU_SCHEDULES,
    MAIN_MENU_STATUS,
    MAIN_MENU_SYSTEM,
    confirm_run_keyboard,
    issues_keyboard,
    projects_keyboard,
    repos_keyboard,
    workspace_source_keyboard,
)

logger = logging.getLogger(__name__)

# Conversation states
(
    RUN_SELECT_REPO,
    RUN_SELECT_ISSUE,
    RUN_SELECT_WORKSPACE,   # NEW: choose clone vs local
    RUN_ENTER_LOCAL_PATH,
    RUN_CONFIRM,
) = range(10, 15)
_MENU_TEXT = filters.Regex(
    r"^(" + "|".join(re.escape(label) for label in (
        MAIN_MENU_GITHUB, MAIN_MENU_PERSONAL, MAIN_MENU_PROJECTS,
        MAIN_MENU_SCHEDULES, MAIN_MENU_STATUS, MAIN_MENU_SYSTEM,
    )) + r")$"
)


def _clear_run_context(context: ContextTypes.DEFAULT_TYPE) -> None:
    if context.user_data is not None:
        for key in list(context.user_data):
            if key.startswith(("run_", "directory_browser_")):
                context.user_data.pop(key, None)


# ---------------------------------------------------------------------------
# Entry: /run command
# ---------------------------------------------------------------------------

@auth_required
async def run_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    _clear_run_context(context)
    projects = project_scanner.scan()
    if projects:
        return await show_project_select_for_run(update, context, page=0)
    assert update.message
    await update.message.reply_text(" Fetching repositories...")
    return await show_repo_select_for_run(update, context)


@auth_required
async def show_run_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Entry when invoked from menu:run."""
    _clear_run_context(context)
    projects = project_scanner.scan()
    if projects:
        return await show_project_select_for_run(update, context, page=0)
    return await show_repo_select_for_run(update, context, page=0)


async def show_project_select_for_run(
    update: Update, context: ContextTypes.DEFAULT_TYPE, page: int = 0
) -> int:
    """Show local projects discovered in PROJECTS_DIR."""
    projects = project_scanner.scan()
    if not projects:
        return await show_repo_select_for_run(update, context, page=page)

    if context.user_data is not None:
        context.user_data["run_scanned_projects"] = projects

    keyboard = projects_keyboard(
        projects,
        page=page,
        callback_prefix="run_proj:",
        page_prefix="run_proj_page:",
        include_cancel=True,
        include_setdir=False,
    )
    # Add option to browse GitHub remote repos
    from telegram import InlineKeyboardButton, InlineKeyboardMarkup
    rows = [list(row) for row in keyboard.inline_keyboard]
    rows.append([
        InlineKeyboardButton("تصفح مستودعات GitHub عن بُعد", callback_data="run_browse_github")
    ])
    keyboard = InlineKeyboardMarkup(rows)

    text = f" *تشغيل الـ Agent — اختر المشروع* (صفحة {page + 1}):\n\nاختر مشروعاً محلياً للعمل عليه:"

    if update.callback_query:
        await update.callback_query.answer()
        await update.callback_query.edit_message_text(
            text, reply_markup=keyboard, parse_mode="Markdown"
        )
    elif update.message:
        await update.message.reply_text(
            text, reply_markup=keyboard, parse_mode="Markdown"
        )
    return RUN_SELECT_REPO


async def run_proj_page_callback(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> int:
    query = update.callback_query
    assert query
    await query.answer()
    page = int((query.data or "").replace("run_proj_page:", ""))
    return await show_project_select_for_run(update, context, page=page)


@auth_required
async def run_proj_selected(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    assert query
    await query.answer()

    raw_val = (query.data or "").replace("run_proj:", "")
    projects = (context.user_data or {}).get("run_scanned_projects")
    if not projects:
        projects = project_scanner.scan()
        if context.user_data is not None:
            context.user_data["run_scanned_projects"] = projects

    project = None
    if raw_val.isdigit():
        idx = int(raw_val)
        if 0 <= idx < len(projects):
            project = projects[idx]
    if not project:
        project = project_scanner.get_project_by_name(raw_val)

    if not project:
        await query.edit_message_text(" لم يتم العثور على المشروع، استخدم /run للبدء من جديد.")
        return ConversationHandler.END

    if not project.repo_full_name:
        await query.edit_message_text(
            f" المشروع `{project.name}` غير مرتبط بمستودع GitHub على الإنترنت.\n\n"
            f"يرجى ربط مستودع GitHub لجلب وحل الـ Issues.",
            parse_mode="Markdown",
        )
        return ConversationHandler.END

    if context.user_data is not None:
        context.user_data["run_repo"] = project.repo_full_name
        context.user_data["run_local_path"] = str(project.path)
        context.user_data["run_issue_page"] = 0

    return await _show_run_issues(update, context, project.repo_full_name, page=0)


async def show_repo_select_for_run(
    update: Update, context: ContextTypes.DEFAULT_TYPE, page: int = 0
) -> int:
    try:
        repos = await asyncio.get_event_loop().run_in_executor(
            None, lambda: github_client.list_repos(page=page, per_page=8)
        )
    except Exception as exc:
        logger.error("Failed to list repos: %s", exc)
        msg = " تعذر جلب المستودعات من GitHub."
        if update.callback_query:
            await update.callback_query.edit_message_text(msg)
        elif update.message:
            await update.message.reply_text(msg)
        return ConversationHandler.END

    keyboard = repos_keyboard(
        repos,
        page=page,
        callback_prefix="run_repo:",
        page_prefix="run_repo_page:",
        include_cancel=True,
    )
    text = f" *تشغيل الـ Agent*\n\nاختر المستودع المطلوب (صفحة {page + 1}):"

    if update.callback_query:
        await update.callback_query.answer()
        await update.callback_query.edit_message_text(
            text, reply_markup=keyboard, parse_mode="Markdown"
        )
    elif update.message:
        await update.message.reply_text(
            text, reply_markup=keyboard, parse_mode="Markdown"
        )
    return RUN_SELECT_REPO


async def run_repo_page_callback(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> int:
    query = update.callback_query
    assert query
    await query.answer()
    page = int((query.data or "").replace("run_repo_page:", ""))
    return await show_repo_select_for_run(update, context, page=page)


# ---------------------------------------------------------------------------
# Step 2: select issue
# ---------------------------------------------------------------------------

async def _show_run_issues(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    full_name: str,
    page: int = 0,
) -> int:
    query = update.callback_query
    assert query
    try:
        issues = await asyncio.get_event_loop().run_in_executor(
            None,
            lambda: issue_service.list_issues(full_name, state="open", page=page, per_page=10),
        )
    except Exception as exc:
        logger.error("Failed to list issues: %s", exc)
        await query.edit_message_text(" تعذر جلب الـ Issues لهذا المستودع.")
        return ConversationHandler.END

    if not issues:
        msg = (
            f" لا توجد Issues مفتوحة في `{full_name}`."
            if page == 0
            else f" لا يوجد المزيد من الـ Issues في `{full_name}`."
        )
        await query.edit_message_text(msg, parse_mode="Markdown")
        return ConversationHandler.END

    keyboard = issues_keyboard(
        issues,
        full_name,
        page=page,
        callback_prefix=CB_ISSUE,
        page_prefix="run_issue_page:",
        include_cancel=True,
    )
    await query.edit_message_text(
        f" *اختيار Issue* — `{full_name}` (صفحة {page + 1})\n\nما هو الـ Issue الذي تريد من الـ Agent حله؟",
        reply_markup=keyboard,
        parse_mode="Markdown",
    )
    return RUN_SELECT_ISSUE


async def run_repo_selected(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    assert query
    full_name = await decode_callback_payload(query, "run_repo:")
    if full_name is None:
        return RUN_SELECT_REPO
    await query.answer()
    if context.user_data is not None:
        context.user_data.pop("run_local_path", None)
        context.user_data["run_repo"] = full_name
        context.user_data["run_issue_page"] = 0

    return await _show_run_issues(update, context, full_name, page=0)


async def run_issue_page_callback(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> int:
    query = update.callback_query
    assert query
    await query.answer()
    page = int((query.data or "").replace("run_issue_page:", ""))
    full_name = (context.user_data or {}).get("run_repo", "")
    if not full_name:
        await query.edit_message_text(" Context lost. Use /run to start again.")
        return ConversationHandler.END
    return await _show_run_issues(update, context, full_name, page=page)


async def run_issue_selected(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    assert query
    await query.answer()

    issue_number = int((query.data or "").replace(CB_ISSUE, ""))
    full_name = (context.user_data or {}).get("run_repo", "")

    if not full_name:
        await query.edit_message_text(" فُقد سياق المستودع، استخدم /run للبدء من جديد.")
        return ConversationHandler.END

    try:
        issue = await asyncio.get_event_loop().run_in_executor(
            None, lambda: issue_service.get_issue(full_name, issue_number)
        )
    except Exception as exc:
        logger.error("Failed to load issue: %s", exc)
        await query.edit_message_text(f" تعذر تحميل الـ Issue #{issue_number}.")
        return ConversationHandler.END

    if context.user_data is not None:
        context.user_data["run_issue"] = issue_number

    # If workspace path is already determined from local project, go straight to confirm
    if context.user_data and context.user_data.get("run_local_path"):
        return await _show_confirm(update, context)

    branch = make_branch_name(issue.number)
    body_preview = (issue.body[:300] + "...") if len(issue.body) > 300 else issue.body

    text = (
        f" *تم اختيار الـ Issue*\n\n"
        f" *المستودع:* `{full_name}`\n"
        f" *الـ Issue:* #{issue.number} — {issue.title}\n"
        f" *الفرع:* `{branch}`\n\n"
        f" *الوصف:*\n{body_preview or '_(فارغ)_'}\n\n"
        f"━━━━━━━━━━━━━━\n"
        f" *أين يقع المشروع؟*\n\n"
        f"اختر ما إذا كنت تريد استنساخ المستودع من GitHub،\n"
        f"أو استخدام مجلد مشروع موجود بالفعل على هذا الجهاز:"
    )

    await query.edit_message_text(
        text,
        reply_markup=workspace_source_keyboard(),
        parse_mode="Markdown",
    )
    return RUN_SELECT_WORKSPACE


# ---------------------------------------------------------------------------
# Step 3: workspace source selection
# ---------------------------------------------------------------------------

async def run_ws_clone_selected(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> int:
    """User chose  Clone from GitHub — skip path entry, go straight to confirm."""
    query = update.callback_query
    assert query
    await query.answer()

    if context.user_data is not None:
        context.user_data["run_local_path"] = None  # None = use clone

    return await _show_confirm(update, context)


async def run_ws_local_selected(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> int:
    """User chose a local workspace — open the directory browser."""
    query = update.callback_query
    assert query
    await query.answer()
    await start_directory_browser(update, context, "استخدام هذا المجلد للمشروع")
    return RUN_ENTER_LOCAL_PATH


@auth_required
async def run_local_browser_navigate(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> int:
    await navigate_directory_browser(update, context)
    return RUN_ENTER_LOCAL_PATH


@auth_required
async def run_local_browser_confirm(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    assert query
    await query.answer()
    path, error = await validate_current_directory(context)
    if path is None:
        await show_browser_error(update, context, error or "تعذر استخدام هذا المجلد.")
        return RUN_ENTER_LOCAL_PATH
    try:
        is_git = (path / ".git").exists()
    except OSError:
        await show_browser_error(update, context, "لا توجد صلاحية لفحص مجلد المشروع.")
        return RUN_ENTER_LOCAL_PATH
    if not is_git:
        await show_browser_error(update, context, "اختر مجلد مشروع Git يحتوي على `.git`.")
        return RUN_ENTER_LOCAL_PATH

    project = project_scanner.inspect_directory(path)
    if context.user_data is not None:
        context.user_data["run_local_path"] = str(path)

    db = context.bot_data.get("db")
    if db is not None and update.effective_user is not None:
        from app.database.repository import SelectedProjectRepository

        await SelectedProjectRepository(db).save(
            update.effective_user.id,
            project.name,
            str(path),
            project.repo_full_name,
        )
    return await _show_confirm(update, context)


# ---------------------------------------------------------------------------
# Step 4: Confirm
# ---------------------------------------------------------------------------

async def _show_confirm(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> int:
    """Show the agent selection + confirm screen (called after workspace chosen)."""
    query = update.callback_query
    assert query

    ud = context.user_data or {}
    full_name = ud.get("run_repo", "")
    issue_number = int(ud.get("run_issue", 0))
    local_path = ud.get("run_local_path")

    branch = make_branch_name(issue_number)
    ws_line = (
        f"*بيئة العمل:* `{local_path}`   مسار محلي"
        if local_path
        else "*بيئة العمل:* مستنسخ من GitHub  "
    )

    text = (
        f" *تأكيد تشغيل الـ AI Agent*\n\n"
        f" *المستودع:* `{full_name}`\n"
        f" *الـ Issue:* #{issue_number}\n"
        f" *الفرع:* `{branch}`\n"
        f"{ws_line}\n\n"
        f"اختر الـ Agent المطلوب لتنفيذ الحل:"
    )

    await query.edit_message_text(
        text,
        reply_markup=confirm_run_keyboard(str(issue_number)),
        parse_mode="Markdown",
    )
    return RUN_CONFIRM


# ---------------------------------------------------------------------------
# Entry from issue detail view
# ---------------------------------------------------------------------------

@auth_required
async def run_issue_from_detail(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> int:
    """Called when user taps [Start Agent] from the issue detail view."""
    query = update.callback_query
    assert query
    await query.answer()

    issue_number = int((query.data or "").replace("run_issue:", ""))
    full_name = (context.user_data or {}).get("selected_repo", "")

    if not full_name:
        await query.edit_message_text(" فُقد سياق المستودع، استخدم /run للبدء من جديد.")
        return ConversationHandler.END

    if context.user_data is not None:
        context.user_data["run_repo"] = full_name
        context.user_data["run_issue"] = issue_number

    try:
        issue = await asyncio.get_event_loop().run_in_executor(
            None, lambda: issue_service.get_issue(full_name, issue_number)
        )
    except Exception:
        await query.edit_message_text(f" تعذر تحميل الـ Issue #{issue_number}.")
        return ConversationHandler.END

    branch = make_branch_name(issue.number)
    text = (
        f" *تم اختيار الـ Issue*\n\n"
        f" *المستودع:* `{full_name}`\n"
        f" *الـ Issue:* #{issue.number} — {issue.title}\n"
        f" *الفرع:* `{branch}`\n\n"
        f"━━━━━━━━━━━━━━\n"
        f" *أين يقع المشروع؟*\n\n"
        f"اختر الاستنساخ من GitHub أو استخدام مجلد محلي على هذا الجهاز:"
    )
    await query.edit_message_text(
        text,
        reply_markup=workspace_source_keyboard(),
        parse_mode="Markdown",
    )
    return RUN_SELECT_WORKSPACE


# ---------------------------------------------------------------------------
# Final step: launch the job
# ---------------------------------------------------------------------------

async def run_confirm(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    assert query
    await query.answer()

    data = query.data or ""

    if data == "cancel":
        await query.edit_message_text(" تم رجوع تشغيل الـ Agent.")
        return ConversationHandler.END

    ud = context.user_data or {}
    full_name = ud.get("run_repo", "")
    issue_number = int(ud.get("run_issue", 0))
    local_path: str | None = ud.get("run_local_path")

    if not full_name or not issue_number:
        await query.edit_message_text(" فُقد سياق المستودع، استخدم /run للبدء من جديد.")
        return ConversationHandler.END

    await query.edit_message_text(" جاري تجهيز المهمة...")

    try:
        repo_info = await asyncio.get_event_loop().run_in_executor(
            None, lambda: github_client.get_repo_info(full_name)
        )
        issue_info = await asyncio.get_event_loop().run_in_executor(
            None, lambda: issue_service.get_issue(full_name, issue_number)
        )
    except Exception as exc:
        logger.error("Failed to load repo/issue for run: %s", exc)
        await query.edit_message_text(" تعذر تحميل تفاصيل المستودع أو الـ Issue.")
        return ConversationHandler.END

    executor = context.bot_data.get("executor")
    if executor is None:
        await query.edit_message_text(" نظام تشغيل المهام (Job Executor) غير مهيأ. يرجى إعادة تشغيل البوت.")
        return ConversationHandler.END

    chat_id = update.effective_chat.id if update.effective_chat else 0

    async def notify(msg: str, reply_markup=None) -> None:
        try:
            from app.utils.security import sanitize_for_telegram
            await context.bot.send_message(
                chat_id=chat_id,
                text=sanitize_for_telegram(msg),
                parse_mode="Markdown",
                reply_markup=reply_markup,
            )
        except Exception as e:
            logger.warning("Failed to send Telegram notification: %s", e)

    # Determine agent name: confirm_run:<agent>:<issue> or confirm_run:<issue>
    agent_name = None
    parts = data.split(":")
    if len(parts) == 3 and parts[0] == "confirm_run":
        agent_name = parts[1]

    try:
        job = await executor.start_job(
            repo_info=repo_info,
            issue_info=issue_info,
            telegram_chat_id=chat_id,
            agent_name=agent_name,
            local_workspace_path=Path(local_path) if local_path else None,
            notify_fn=notify,
        )
        agent_label = f" ({agent_name})" if agent_name else ""
        ws_label = f" مسار محلي: `{local_path}`" if local_path else " مستنسخ من GitHub"
        from telegram import InlineKeyboardButton, InlineKeyboardMarkup
        run_kb = InlineKeyboardMarkup([
            [
                InlineKeyboardButton("متابعة الحالة", callback_data="menu:status"),
            ]
        ])
        await query.edit_message_text(
            f" *بدء المهمة بنجاح!*{agent_label}\n"
            "━━━━━━━━━━━━━━━━━━━━\n"
            f" *معرف العملية:* `{job.job_id}`\n"
            f" *المستودع:* `{full_name}`\n"
            f" *الـ Issue:* `#{issue_number}`\n"
            f" *بيئة العمل:* {ws_label}\n"
            "━━━━━━━━━━━━━━━━━━━━\n"
            " سأرسل لك تحديثات فورية حول مراحل التحليل والحل...",
            reply_markup=run_kb,
            parse_mode="Markdown",
        )
    except RuntimeError as exc:
        await query.edit_message_text(f" {exc}")
    except Exception as exc:
        logger.error("Failed to start job: %s", exc)
        await query.edit_message_text(f" تعذر بدء المهمة: {str(exc)[:200]}")

    return ConversationHandler.END


# ---------------------------------------------------------------------------
# Cancel
# ---------------------------------------------------------------------------

async def run_to_menu(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    _clear_run_context(context)
    if update.callback_query:
        await update.callback_query.answer()
        await update.callback_query.edit_message_text("⬅️ تم الرجوع.")
    return ConversationHandler.END


@auth_required
async def run_main_menu(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    from app.telegram.handlers.start import main_menu_message_handler

    _clear_run_context(context)
    await main_menu_message_handler(update, context)
    return ConversationHandler.END


@auth_required
async def run_saved_proj_selected(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    from telegram import InlineKeyboardButton, InlineKeyboardMarkup

    from app.database.repository import SavedProjectRepository

    query = update.callback_query
    assert query
    await query.answer()
    db = context.bot_data.get("db")
    user = update.effective_user
    try:
        project_id = int((query.data or "").removeprefix("run_saved_proj:"))
    except ValueError:
        await query.edit_message_text("اختيار المشروع غير صالح. أعد فتح قائمة المشاريع.")
        return ConversationHandler.END
    if db is None or user is None:
        await query.edit_message_text("قاعدة بيانات المشاريع غير متاحة.")
        return ConversationHandler.END
    project = await SavedProjectRepository(db).get_by_id(project_id)
    if project is None or project.user_id != user.id:
        await query.edit_message_text("المشروع غير متاح. أعد اختياره من قائمة مشاريعك.")
        return ConversationHandler.END
    path, error = await asyncio.to_thread(directory_browser.validate_directory, Path(project.path))
    if path is None or error is not None:
        await query.edit_message_text("تعذر الوصول إلى مجلد المشروع المحفوظ. حدّث موقعه من المشاريع المحلية.")
        return ConversationHandler.END
    if not (path / ".git").exists():
        await query.edit_message_text("المجلد المحفوظ لم يعد مستودع Git. حدّث موقع المشروع.")
        return ConversationHandler.END
    if not project.repo_full_name:
        await query.edit_message_text(
            "هذا المشروع غير مرتبط بمستودع GitHub. يمكنك تشغيل Prompt محلي بدل اختيار Issue.",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("جدولة Prompt محلي", callback_data="menu:schedule:local")],
            ]),
        )
        return ConversationHandler.END
    _clear_run_context(context)
    if context.user_data is not None:
        context.user_data["run_repo"] = project.repo_full_name
        context.user_data["run_local_path"] = str(path)
        context.user_data["run_issue_page"] = 0
    return await _show_run_issues(update, context, project.repo_full_name, page=0)


async def run_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    _clear_run_context(context)
    if update.callback_query:
        await update.callback_query.answer()
        await update.callback_query.edit_message_text(" تم رجوع تشغيل الـ Agent.")
    elif update.message:
        await update.message.reply_text(" تم رجوع تشغيل الـ Agent.")
    return ConversationHandler.END


# ---------------------------------------------------------------------------
# Conversation handler assembly
# ---------------------------------------------------------------------------

def build_run_handler() -> ConversationHandler:
    return ConversationHandler(
        entry_points=[
            CommandHandler("run", run_command),
            CallbackQueryHandler(show_run_start, pattern="^menu:run$"),
            CallbackQueryHandler(run_issue_from_detail, pattern="^run_issue:\\d+$"),
            CallbackQueryHandler(run_proj_selected, pattern="^run_proj:"),
            CallbackQueryHandler(run_saved_proj_selected, pattern=r"^run_saved_proj:\d+$"),
            CallbackQueryHandler(
                lambda u, c: _run_for_repo(u, c),
                pattern="^run_for:",
            ),
        ],
        states={
            RUN_SELECT_REPO: [
                CallbackQueryHandler(run_proj_page_callback, pattern="^run_proj_page:"),
                CallbackQueryHandler(run_proj_selected, pattern="^run_proj:"),
                CallbackQueryHandler(
                    lambda u, c: show_repo_select_for_run(u, c, 0),
                    pattern="^run_browse_github$",
                ),
                CallbackQueryHandler(run_repo_page_callback, pattern="^run_repo_page:"),
                CallbackQueryHandler(run_repo_selected, pattern="^run_repo:"),
                CallbackQueryHandler(run_cancel, pattern="^cancel$"),
            ],
            RUN_SELECT_ISSUE: [
                CallbackQueryHandler(run_issue_page_callback, pattern="^run_issue_page:"),
                CallbackQueryHandler(run_issue_selected, pattern=f"^{CB_ISSUE}\\d+$"),
                CallbackQueryHandler(run_cancel, pattern="^cancel$"),
            ],
            RUN_SELECT_WORKSPACE: [
                CallbackQueryHandler(run_ws_clone_selected, pattern="^ws:clone$"),
                CallbackQueryHandler(run_ws_local_selected, pattern="^ws:local$"),
                CallbackQueryHandler(run_cancel, pattern="^cancel$"),
            ],
            RUN_ENTER_LOCAL_PATH: [
                CallbackQueryHandler(run_local_browser_navigate, pattern=DIRECTORY_NAVIGATION_PATTERN),
                CallbackQueryHandler(run_local_browser_confirm, pattern=f"^{CB_DIRECTORY_CONFIRM}$"),
            ],
            RUN_CONFIRM: [
                CallbackQueryHandler(run_confirm, pattern="^(confirm_run:|cancel)"),
            ],
        },
        fallbacks=[
            MessageHandler(_MENU_TEXT, run_main_menu),
            CommandHandler("cancel", run_cancel),
            CallbackQueryHandler(run_cancel, pattern="^cancel$"),
            CallbackQueryHandler(run_to_menu, pattern="^menu:start$"),
        ],
        per_user=True,
        per_chat=True,
        per_message=False,
    )


@auth_required
async def _run_for_repo(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Handle 'Run Agent' button from repo detail view."""
    query = update.callback_query
    assert query
    full_name = await decode_callback_payload(query, "run_for:")
    if full_name is None:
        return ConversationHandler.END
    await query.answer()
    _clear_run_context(context)
    if context.user_data is not None:
        context.user_data["run_repo"] = full_name
    return await _show_run_issues(update, context, full_name, page=0)
