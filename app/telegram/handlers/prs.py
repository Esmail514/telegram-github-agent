"""
/prs and PR merge handler — browse and merge GitHub Pull Requests.
"""
from __future__ import annotations

import asyncio
import logging
from typing import Literal

from telegram import Update
from telegram.ext import ContextTypes

from app.github.client import github_client
from app.github.pull_requests import pr_service
from app.runner.project_scanner import project_scanner
from app.telegram.auth import auth_required
from app.telegram.keyboards import (
    merge_options_keyboard,
    pr_detail_keyboard,
    projects_keyboard,
    prs_keyboard,
    repos_keyboard,
)

logger = logging.getLogger(__name__)

_PRS_PER_PAGE = 10


@auth_required
async def prs_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle /prs command — show repository selector or direct PRs."""
    projects = project_scanner.scan()
    if projects:
        await show_project_select_for_prs(update, context, page=0)
        return
    assert update.message
    await update.message.reply_text("⏳ جاري جلب المستودعات من GitHub...")
    await show_repo_select_for_prs(update, context)


async def show_project_select_for_prs(
    update: Update, context: ContextTypes.DEFAULT_TYPE, page: int = 0
) -> None:
    projects = project_scanner.scan()
    if not projects:
        await show_repo_select_for_prs(update, context, page=page)
        return

    if context.user_data is not None:
        context.user_data["prs_scanned_projects"] = projects

    keyboard = projects_keyboard(
        projects,
        page=page,
        callback_prefix="prs_proj:",
        page_prefix="prs_proj_page:",
        include_cancel=True,
        include_setdir=False,
    )
    from telegram import InlineKeyboardButton
    keyboard.inline_keyboard.insert(-1, [
        InlineKeyboardButton("🌐 Browse All GitHub Repos", callback_data="prs_browse_github")
    ])

    text = f"🔀 *Pull Requests — Select Project* (page {page + 1})\n\nChoose a project to view open PRs:"
    if update.callback_query:
        await update.callback_query.edit_message_text(text, reply_markup=keyboard, parse_mode="Markdown")
    elif update.message:
        await update.message.reply_text(text, reply_markup=keyboard, parse_mode="Markdown")


@auth_required
async def prs_proj_page_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    assert query
    await query.answer()
    page = int((query.data or "").replace("prs_proj_page:", ""))
    await show_project_select_for_prs(update, context, page=page)


@auth_required
async def prs_proj_selected_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    assert query
    await query.answer()

    raw_val = (query.data or "").replace("prs_proj:", "")
    projects = (context.user_data or {}).get("prs_scanned_projects") or project_scanner.scan()

    project = None
    if raw_val.isdigit():
        idx = int(raw_val)
        if 0 <= idx < len(projects):
            project = projects[idx]
    if not project:
        project = project_scanner.get_project_by_name(raw_val)

    if not project or not project.repo_full_name:
        await query.edit_message_text(
            f"⚠️ المشروع `{project.name if project else raw_val}` غير مرتبط بمستودع GitHub.\n"
            f"لا يمكن جلب طلبات السحب (PRs) لهذا المجلد.",
            parse_mode="Markdown",
        )
        return

    await show_prs_for_repo(update, context, project.repo_full_name, page=0)


@auth_required
async def prs_browse_github_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    assert query
    await query.answer()
    await show_repo_select_for_prs(update, context, page=0)


async def show_repo_select_for_prs(
    update: Update, context: ContextTypes.DEFAULT_TYPE, page: int = 0
) -> None:
    """Show repository selector for pull requests."""
    try:
        repos = await asyncio.get_event_loop().run_in_executor(
            None, lambda: github_client.list_repos(page=page, per_page=8)
        )
    except Exception as exc:
        logger.error("Failed to list repos for PRs: %s", exc)
        msg = "❌ تعذر جلب المستودعات من GitHub."
        if update.callback_query:
            await update.callback_query.edit_message_text(msg)
        elif update.message:
            await update.message.reply_text(msg)
        return

    keyboard = repos_keyboard(
        repos,
        page=page,
        callback_prefix="prs_for:",
        page_prefix="prs_repo_page:",
        include_cancel=True,
    )
    text = (
        f"🔀 *طلبات السحب (Pull Requests)* (صفحة {page + 1})\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        "اختر مستودعاً لعرض ومتابعة طلبات السحب المفتوحة:"
    )

    if update.callback_query:
        await update.callback_query.edit_message_text(
            text, reply_markup=keyboard, parse_mode="Markdown"
        )
    elif update.message:
        await update.message.reply_text(
            text, reply_markup=keyboard, parse_mode="Markdown"
        )


@auth_required
async def prs_repo_page_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Pagination for PR repository selector."""
    query = update.callback_query
    assert query
    await query.answer()
    page = int((query.data or "").replace("prs_repo_page:", ""))
    await show_repo_select_for_prs(update, context, page=page)


@auth_required
async def prs_for_repo_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """User picked a repo — show its open pull requests."""
    query = update.callback_query
    assert query
    await query.answer()

    full_name = (query.data or "").replace("prs_for:", "")
    if context.user_data is not None:
        context.user_data["selected_pr_repo"] = full_name

    await show_prs_for_repo(query, context, full_name, page=0)


async def show_prs_for_repo(
    query, context: ContextTypes.DEFAULT_TYPE, full_name: str, page: int = 0
) -> None:
    """Load and display pull requests for a given repository."""
    await query.edit_message_text(f"⏳ جاري جلب طلبات السحب لـ `{full_name}`...", parse_mode="Markdown")

    try:
        prs = await asyncio.get_event_loop().run_in_executor(
            None, lambda: pr_service.list_prs(full_name, state="open", page=page, per_page=_PRS_PER_PAGE)
        )
    except Exception as exc:
        logger.error("Failed to fetch PRs for %s: %s", full_name, exc)
        await query.edit_message_text(f"❌ تعذر تحميل طلبات السحب: {str(exc)[:200]}")
        return

    if not prs:
        await query.edit_message_text(
            f"🔀 لم يتم العثور على طلبات سحب مفتوحة في `{full_name}`.",
            parse_mode="Markdown",
        )
        return

    keyboard = prs_keyboard(prs, repo_full_name=full_name, page=page, include_cancel=True)
    await query.edit_message_text(
        f"🔀 *طلبات السحب في `{full_name}`* (صفحة {page + 1})\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        "اختر Pull Request لعرض التفاصيل أو دمجه:",
        reply_markup=keyboard,
        parse_mode="Markdown",
    )


@auth_required
async def pr_page_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Pagination for pull requests within a repository."""
    query = update.callback_query
    assert query
    await query.answer()

    repo = (context.user_data or {}).get("selected_pr_repo", "")
    page = int((query.data or "").replace("pr_page:", ""))

    if not repo:
        await query.edit_message_text("❌ فُقد سياق المستودع، يرجى كتابة /prs للاختيار من جديد.")
        return

    await show_prs_for_repo(query, context, repo, page=page)


@auth_required
async def pr_selected_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """User tapped a PR from the list — show PR details."""
    query = update.callback_query
    assert query
    await query.answer()

    pr_number = int((query.data or "").replace("pr:", ""))
    repo = (context.user_data or {}).get("selected_pr_repo", "")

    if not repo:
        await query.edit_message_text("❌ فُقد سياق المستودع، يرجى كتابة /prs للاختيار من جديد.")
        return

    await query.edit_message_text(f"⏳ جاري تحميل تفاصيل طلب السحب #{pr_number}...")

    try:
        pr = await asyncio.get_event_loop().run_in_executor(
            None, lambda: pr_service.get_pr(repo, pr_number)
        )
    except Exception as exc:
        logger.error("Failed to fetch PR #%d: %s", pr_number, exc)
        await query.edit_message_text(f"❌ تعذر جلب تفاصيل طلب السحب #{pr_number}: {str(exc)[:200]}")
        return

    author_str = f"بواسطة `@{pr.user_login}`" if pr.user_login else ""
    text = (
        f"🔀 *Pull Request #{pr.number} | {pr.state.upper()}*\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        f"💡 *{pr.title}*\n"
        f"👤 {author_str}\n"
        f"🌿 *الفرع:* `{pr.head_branch}` ➔ `{pr.base_branch}`\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        f"🔗 [فتح الـ PR على GitHub]({pr.html_url})"
    )

    await query.edit_message_text(
        text,
        reply_markup=pr_detail_keyboard(pr, repo_full_name=repo),
        parse_mode="Markdown",
    )


@auth_required
async def merge_pr_start_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    User tapped "🔀 Merge Pull Request".
    Show merge options keyboard (Squash, Merge commit, Rebase).
    Callback data: merge_pr:<repo>:<pr_number>
    """
    query = update.callback_query
    assert query
    await query.answer()

    # Data: merge_pr:<repo_full_name>:<pr_number>
    raw_data = (query.data or "").replace("merge_pr:", "")
    parts = raw_data.rsplit(":", 1)
    if len(parts) != 2:
        await query.edit_message_text("❌ طلب دمج غير صالح.")
        return

    repo, pr_number_str = parts
    pr_number = int(pr_number_str)

    text = (
        f"🔀 *دمج طلب السحب #{pr_number}*\n\n"
        f"المستودع: `{repo}`\n\n"
        f"اختر استراتيجية الدمج المفضلة:"
    )

    await query.edit_message_text(
        text,
        reply_markup=merge_options_keyboard(repo_full_name=repo, pr_number=pr_number),
        parse_mode="Markdown",
    )


@auth_required
async def merge_pr_execute_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    User chose a merge strategy — execute the merge on GitHub.
    Callback data: do_merge:<method>:<repo>:<pr_number>
    """
    query = update.callback_query
    assert query
    await query.answer()

    raw_data = (query.data or "").replace("do_merge:", "")
    parts = raw_data.split(":", 2)
    if len(parts) != 3:
        await query.edit_message_text("❌ طلب تنفيذ دمج غير صالح.")
        return

    method_str, repo, pr_number_str = parts
    pr_number = int(pr_number_str)
    method: Literal["merge", "squash", "rebase"] = (
        method_str if method_str in ("merge", "squash", "rebase") else "squash"  # type: ignore[assignment]
    )

    await query.edit_message_text(f"⏳ جاري دمج طلب السحب #{pr_number} باستخدام `{method}`...")

    result = await asyncio.get_event_loop().run_in_executor(
        None,
        lambda: pr_service.merge_pr(
            repo_full_name=repo,
            number=pr_number,
            commit_title=f"Merge PR #{pr_number} via Telegram",
            merge_method=method,
        ),
    )

    if result.merged:
        sha_display = f"\n*Commit SHA:* `{result.sha[:7]}`" if result.sha else ""
        await query.edit_message_text(
            f"🎉 *تم دمج طلب السحب #{pr_number} بنجاح!*\n\n"
            f"*المستودع:* `{repo}`\n"
            f"*الاستراتيجية:* `{method}`{sha_display}\n\n"
            f"أصبحت التغييرات الآن مدمجة في الفرع الافتراضي الرئيسي.",
            parse_mode="Markdown",
        )
    else:
        await query.edit_message_text(
            f"❌ *فشل دمج طلب السحب #{pr_number}*\n\n"
            f"*المستودع:* `{repo}`\n"
            f"*الخطأ:* {result.message}\n\n"
            f"يرجى مراجعة إعدادات حماية الفرع أو فحص وجود تعارضات دمج (Merge Conflicts) على GitHub.",
            parse_mode="Markdown",
        )
