"""
/repos handler — list and select GitHub repositories.
"""
from __future__ import annotations

import logging

from telegram import Update
from telegram.ext import ContextTypes

from app.github.client import RepoInfo, github_client
from app.telegram.auth import auth_required
from app.telegram.keyboards import CB_REPO, CB_REPO_PAGE, repos_keyboard

logger = logging.getLogger(__name__)

_REPOS_PER_PAGE = 8


@auth_required
async def repos_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    assert update.message
    await update.message.reply_text("⏳ جاري جلب المستودعات من GitHub...")
    await show_repos(update, context, page=0)


async def show_repos(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    page: int = 0,
) -> None:
    """Fetch repos and display them as an inline keyboard."""
    try:
        repos = await _fetch_repos(page)
    except Exception as exc:
        logger.error("Failed to list repos: %s", exc)
        msg = "❌ تعذر جلب المستودعات. يرجى التحقق من بيانات الربط مع GitHub."
        if update.callback_query:
            await update.callback_query.edit_message_text(msg)
        elif update.message:
            await update.message.reply_text(msg)
        return

    if not repos:
        msg = "📦 لم يتم العثور على أي مستودعات." if page == 0 else "📦 لا يوجد المزيد من المستودعات."
        if update.callback_query:
            await update.callback_query.edit_message_text(msg)
        elif update.message:
            await update.message.reply_text(msg)
        return

    keyboard = repos_keyboard(repos, page=page, include_cancel=True)
    text = (
        f"📦 *مستودعات GitHub | Repositories* (صفحة {page + 1})\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        "اختر مستودعاً من القائمة أدناه لعرض تفاصيله أو تشغيل الـ Agent:"
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
async def repo_page_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    assert query
    await query.answer()
    page = int((query.data or "").replace(CB_REPO_PAGE, ""))
    await show_repos(update, context, page=page)


@auth_required
async def repo_selected_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """User tapped a repository — store selection and show repo info."""
    query = update.callback_query
    assert query
    await query.answer()

    full_name = (query.data or "").replace(CB_REPO, "")
    if context.user_data is not None:
        context.user_data["selected_repo"] = full_name

    try:
        repo = github_client.get_repo_info(full_name)
        text = (
            f"📦 *{repo.name}*\n"
            "━━━━━━━━━━━━━━━━━━━━\n"
            f"🔗 `{repo.full_name}`\n"
            f"📝 {repo.description or '_(لا يوجد وصف)_'}\n\n"
            f"🌿 *الفرع الافتراضي:* `{repo.default_branch}`\n"
            f"🔐 *النوع:* {'🔒 خاص (Private)' if repo.private else '🌐 عام (Public)'}\n"
            "━━━━━━━━━━━━━━━━━━━━\n"
            "اختر الإجراء المطلوب:"
        )
        from telegram import InlineKeyboardButton, InlineKeyboardMarkup
        keyboard = InlineKeyboardMarkup([
            [
                InlineKeyboardButton("📋 عرض الـ Issues", callback_data=f"issues_for:{full_name}"),
                InlineKeyboardButton("➕ Issue جديد", callback_data=f"newissue_for:{full_name}"),
            ],
            [
                InlineKeyboardButton("🚀 تشغيل الـ Agent", callback_data=f"run_for:{full_name}"),
                InlineKeyboardButton("🔀 الـ Pull Requests", callback_data=f"prs_for:{full_name}"),
            ],
            [
                InlineKeyboardButton("◀ رجوع للمستودعات", callback_data="menu:repos"),
                InlineKeyboardButton("🏠 القائمة الرئيسية", callback_data="menu:start"),
            ],
        ])
        await query.edit_message_text(text, reply_markup=keyboard, parse_mode="Markdown")
    except Exception as exc:
        logger.error("Failed to get repo info for %s: %s", full_name, exc)
        await query.edit_message_text(f"❌ تعذر تحميل بيانات المستودع: `{full_name}`", parse_mode="Markdown")


async def _fetch_repos(page: int) -> list[RepoInfo]:
    import asyncio
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(
        None, lambda: github_client.list_repos(page=page, per_page=_REPOS_PER_PAGE)
    )
