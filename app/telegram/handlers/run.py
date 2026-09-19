"""
/run handler — launch an AI agent job for a selected issue.

Flow: Select Repo → Select Issue → Confirm → Launch Agent
"""
from __future__ import annotations

import asyncio
import logging

from telegram import Update
from telegram.ext import (
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    ConversationHandler,
)

from app.github.client import github_client
from app.github.issues import issue_service
from app.runner.jobs import make_branch_name
from app.telegram.auth import auth_required
from app.telegram.keyboards import (
    CB_ISSUE,
    confirm_run_keyboard,
    issues_keyboard,
    repos_keyboard,
)

logger = logging.getLogger(__name__)

(
    RUN_SELECT_REPO,
    RUN_SELECT_ISSUE,
    RUN_CONFIRM,
) = range(10, 13)


@auth_required
async def run_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    assert update.message
    await update.message.reply_text("⏳ Fetching repositories...")
    return await show_repo_select_for_run(update, context)


async def show_repo_select_for_run(
    update: Update, context: ContextTypes.DEFAULT_TYPE, page: int = 0
) -> int:
    try:
        repos = await asyncio.get_event_loop().run_in_executor(
            None, lambda: github_client.list_repos(page=page, per_page=8)
        )
    except Exception as exc:
        logger.error("Failed to list repos: %s", exc)
        msg = "❌ Could not fetch repositories."
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
    text = f"🚀 *Run Agent*\n\nSelect a repository (page {page + 1}):"

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
        await query.edit_message_text("❌ Could not fetch issues for this repository.")
        return ConversationHandler.END

    if not issues:
        msg = (
            f"📋 No open issues in `{full_name}`."
            if page == 0
            else f"📋 No more open issues in `{full_name}`."
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
        f"📋 *Select Issue* — `{full_name}` (page {page + 1})\n\nWhich issue should the agent implement?",
        reply_markup=keyboard,
        parse_mode="Markdown",
    )
    return RUN_SELECT_ISSUE


async def run_repo_selected(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    assert query
    await query.answer()

    full_name = (query.data or "").replace("run_repo:", "")
    if context.user_data is not None:
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
        await query.edit_message_text("❌ Context lost. Use /run to start again.")
        return ConversationHandler.END
    return await _show_run_issues(update, context, full_name, page=page)


async def run_issue_selected(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    assert query
    await query.answer()

    issue_number = int((query.data or "").replace(CB_ISSUE, ""))
    full_name = (context.user_data or {}).get("run_repo", "")

    if not full_name:
        await query.edit_message_text("❌ Context lost. Use /run to start again.")
        return ConversationHandler.END

    try:
        issue = await asyncio.get_event_loop().run_in_executor(
            None, lambda: issue_service.get_issue(full_name, issue_number)
        )
    except Exception as exc:
        logger.error("Failed to load issue: %s", exc)
        await query.edit_message_text(f"❌ Could not load issue #{issue_number}.")
        return ConversationHandler.END

    if context.user_data is not None:
        context.user_data["run_issue"] = issue_number

    branch = make_branch_name(issue.number)
    body_preview = (issue.body[:300] + "...") if len(issue.body) > 300 else issue.body

    text = (
        f"🚀 *Start AI Agent?*\n\n"
        f"*Repository:* `{full_name}`\n"
        f"*Issue:* #{issue.number}\n"
        f"*Title:* {issue.title}\n"
        f"*Branch:* `{branch}`\n\n"
        f"*Description:*\n{body_preview or '(empty)'}"
    )

    # Use issue_number as a simple preview token
    keyboard = confirm_run_keyboard(str(issue.number))
    await query.edit_message_text(text, reply_markup=keyboard, parse_mode="Markdown")
    return RUN_CONFIRM


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
        await query.edit_message_text("❌ Context lost. Use /run to start again.")
        return ConversationHandler.END

    if context.user_data is not None:
        context.user_data["run_repo"] = full_name
        context.user_data["run_issue"] = issue_number

    try:
        issue = await asyncio.get_event_loop().run_in_executor(
            None, lambda: issue_service.get_issue(full_name, issue_number)
        )
    except Exception:
        await query.edit_message_text(f"❌ Could not load issue #{issue_number}.")
        return ConversationHandler.END

    branch = make_branch_name(issue.number)
    text = (
        f"🚀 *Start AI Agent?*\n\n"
        f"*Repository:* `{full_name}`\n"
        f"*Issue:* #{issue.number} — {issue.title}\n"
        f"*Branch:* `{branch}`"
    )
    keyboard = confirm_run_keyboard(str(issue.number))
    await query.edit_message_text(text, reply_markup=keyboard, parse_mode="Markdown")
    return RUN_CONFIRM


async def run_confirm(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    assert query
    await query.answer()

    data = query.data or ""

    if data == "cancel":
        await query.edit_message_text("❌ Agent run cancelled.")
        return ConversationHandler.END

    # data = "confirm_run:<issue_number>"
    ud = context.user_data or {}
    full_name = ud.get("run_repo", "")
    issue_number = int(ud.get("run_issue", 0))

    if not full_name or not issue_number:
        await query.edit_message_text("❌ Context lost. Use /run to start again.")
        return ConversationHandler.END

    await query.edit_message_text("⏳ Preparing job...")

    try:
        repo_info = await asyncio.get_event_loop().run_in_executor(
            None, lambda: github_client.get_repo_info(full_name)
        )
        issue_info = await asyncio.get_event_loop().run_in_executor(
            None, lambda: issue_service.get_issue(full_name, issue_number)
        )
    except Exception as exc:
        logger.error("Failed to load repo/issue for run: %s", exc)
        await query.edit_message_text("❌ Could not load repository or issue details.")
        return ConversationHandler.END

    # Get the job executor from bot_data
    executor = context.bot_data.get("executor")
    if executor is None:
        await query.edit_message_text("❌ Job executor not initialised. Restart the bot.")
        return ConversationHandler.END

    chat_id = query.message.chat_id if query.message else 0

    async def notify(msg: str) -> None:
        try:
            from app.utils.security import sanitize_for_telegram
            await context.bot.send_message(
                chat_id=chat_id,
                text=sanitize_for_telegram(msg),
                parse_mode="Markdown",
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
            notify_fn=notify,
        )
        agent_label = f" ({agent_name})" if agent_name else ""
        await query.edit_message_text(
            f"✅ *Job queued!*{agent_label}\n\n"
            f"Job ID: `{job.job_id}`\n"
            f"Repository: `{full_name}`\n"
            f"Issue: #{issue_number}\n\n"
            f"I'll send progress updates as the agent works.",
            parse_mode="Markdown",
        )
    except RuntimeError as exc:
        # Another job already active
        await query.edit_message_text(f"⚠️ {exc}")
    except Exception as exc:
        logger.error("Failed to start job: %s", exc)
        await query.edit_message_text(f"❌ Failed to start job: {str(exc)[:200]}")

    return ConversationHandler.END


async def run_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    if update.callback_query:
        await update.callback_query.answer()
        await update.callback_query.edit_message_text("❌ Agent run cancelled.")
    elif update.message:
        await update.message.reply_text("❌ Agent run cancelled.")
    return ConversationHandler.END


def build_run_handler() -> ConversationHandler:
    return ConversationHandler(
        entry_points=[
            CommandHandler("run", run_command),
            CallbackQueryHandler(show_repo_select_for_run, pattern="^menu:run$"),
            CallbackQueryHandler(run_issue_from_detail, pattern="^run_issue:\\d+$"),
            CallbackQueryHandler(
                lambda u, c: _run_for_repo(u, c),
                pattern="^run_for:",
            ),
        ],
        states={
            RUN_SELECT_REPO: [
                CallbackQueryHandler(run_repo_page_callback, pattern="^run_repo_page:"),
                CallbackQueryHandler(run_repo_selected, pattern="^run_repo:"),
                CallbackQueryHandler(run_cancel, pattern="^cancel$"),
            ],
            RUN_SELECT_ISSUE: [
                CallbackQueryHandler(run_issue_page_callback, pattern="^run_issue_page:"),
                CallbackQueryHandler(run_issue_selected, pattern=f"^{CB_ISSUE}\\d+$"),
                CallbackQueryHandler(run_cancel, pattern="^cancel$"),
            ],
            RUN_CONFIRM: [
                CallbackQueryHandler(run_confirm, pattern="^(confirm_run:|cancel)"),
            ],
        },
        fallbacks=[
            CommandHandler("cancel", run_cancel),
            CallbackQueryHandler(run_cancel, pattern="^cancel$"),
        ],
        per_user=True,
        per_chat=True,
        per_message=False,
    )


async def _run_for_repo(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Handle 'Run Agent' button from repo detail view."""
    query = update.callback_query
    assert query
    full_name = (query.data or "").replace("run_for:", "")
    if context.user_data is not None:
        context.user_data["run_repo"] = full_name
    # Simulate as if repo was selected in the run flow
    query.data = f"run_repo:{full_name}"
    return await run_repo_selected(update, context)
