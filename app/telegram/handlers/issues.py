"""
/issues handler — browse and view GitHub issues.
"""
from __future__ import annotations

import asyncio
import logging

from telegram import Update
from telegram.ext import ContextTypes

from app.github.client import github_client
from app.github.issues import issue_service
from app.telegram.auth import auth_required
from app.telegram.keyboards import (
    CB_ISSUE,
    CB_ISSUE_PAGE,
    issue_detail_keyboard,
    issues_keyboard,
    repos_keyboard,
)

logger = logging.getLogger(__name__)

_ISSUES_PER_PAGE = 10


@auth_required
async def issues_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    assert update.message
    await update.message.reply_text("⏳ Fetching repositories...")
    await show_repo_select_for_issues(update, context)


async def show_repo_select_for_issues(
    update: Update, context: ContextTypes.DEFAULT_TYPE, page: int = 0
) -> None:
    """Show repo selector configured to then show issues."""
    try:
        repos = await asyncio.get_event_loop().run_in_executor(
            None, lambda: github_client.list_repos(page=page, per_page=8)
        )
    except Exception as exc:
        logger.error("Failed to list repos: %s", exc)
        msg = "❌ Failed to fetch repositories."
        if update.callback_query:
            await update.callback_query.edit_message_text(msg)
        elif update.message:
            await update.message.reply_text(msg)
        return

    keyboard = repos_keyboard(
        repos,
        page=page,
        callback_prefix="issues_for:",
        page_prefix="issues_repo_page:",
        include_cancel=True,
    )
    text = "📋 *Select Repository* to view issues"
    if update.callback_query:
        await update.callback_query.edit_message_text(
            text, reply_markup=keyboard, parse_mode="Markdown"
        )
    elif update.message:
        await update.message.reply_text(
            text, reply_markup=keyboard, parse_mode="Markdown"
        )


@auth_required
async def issues_repo_page_callback(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    query = update.callback_query
    assert query
    await query.answer()
    page = int((query.data or "").replace("issues_repo_page:", ""))
    await show_repo_select_for_issues(update, context, page=page)


@auth_required
async def issues_for_repo_callback(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    """User selected a repo from issues menu — show issue list."""
    query = update.callback_query
    assert query
    await query.answer()

    data = query.data or ""
    full_name = data.replace("issues_for:", "")

    if context.user_data is not None:
        context.user_data["selected_repo"] = full_name
        context.user_data["issues_page"] = 0

    await _show_issues(update, context, full_name, page=0)


@auth_required
async def issue_page_callback(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    query = update.callback_query
    assert query
    await query.answer()

    page = int((query.data or "").replace(CB_ISSUE_PAGE, ""))
    full_name = (context.user_data or {}).get("selected_repo", "")
    if not full_name:
        await query.edit_message_text("❌ Repository context lost. Use /issues to start again.")
        return
    await _show_issues(update, context, full_name, page=page)


@auth_required
async def issue_selected_callback(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    """User tapped an issue — show detail view."""
    query = update.callback_query
    assert query
    await query.answer()

    issue_number = int((query.data or "").replace(CB_ISSUE, ""))
    full_name = (context.user_data or {}).get("selected_repo", "")

    if not full_name:
        await query.edit_message_text("❌ Repository context lost. Use /issues to start again.")
        return

    try:
        issue = await asyncio.get_event_loop().run_in_executor(
            None, lambda: issue_service.get_issue(full_name, issue_number)
        )
    except Exception as exc:
        logger.error("Failed to get issue #%d from %s: %s", issue_number, full_name, exc)
        await query.edit_message_text(f"❌ Could not load issue #{issue_number}.")
        return

    if context.user_data is not None:
        context.user_data["selected_issue"] = issue_number

    labels_str = ", ".join(f"`{lb}`" for lb in issue.labels) if issue.labels else "none"
    body_preview = (issue.body[:400] + "...") if len(issue.body) > 400 else issue.body

    text = (
        f"📌 *Issue #{issue.number}*\n\n"
        f"*{issue.title}*\n\n"
        f"*Description:*\n{body_preview or '(empty)'}\n\n"
        f"*Status:* {issue.state.upper()}\n"
        f"*Labels:* {labels_str}"
    )

    await query.edit_message_text(
        text,
        reply_markup=issue_detail_keyboard(issue),
        parse_mode="Markdown",
    )


async def _show_issues(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    full_name: str,
    page: int,
) -> None:
    try:
        issues = await asyncio.get_event_loop().run_in_executor(
            None,
            lambda: issue_service.list_issues(
                full_name, state="open", page=page, per_page=_ISSUES_PER_PAGE
            ),
        )
    except Exception as exc:
        logger.error("Failed to list issues for %s: %s", full_name, exc)
        msg = f"❌ Could not fetch issues for `{full_name}`."
        if update.callback_query:
            await update.callback_query.edit_message_text(msg, parse_mode="Markdown")
        return

    if not issues:
        msg = f"📋 No open issues in `{full_name}`." if page == 0 else "📋 No more issues."
        if update.callback_query:
            await update.callback_query.edit_message_text(msg, parse_mode="Markdown")
        return

    keyboard = issues_keyboard(issues, full_name, page=page, include_cancel=True)
    text = f"📋 *Open Issues — {full_name}* (page {page + 1})\n\nSelect an issue:"

    if update.callback_query:
        await update.callback_query.edit_message_text(
            text, reply_markup=keyboard, parse_mode="Markdown"
        )
